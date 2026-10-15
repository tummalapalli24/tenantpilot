"""MCP client used by the agent runtime and the control plane.

* ``catalog()``  - discovers tools via MCP ``tools/list`` (cached briefly) and normalizes
  them: name, description, JSON input schema, read-only flag (from the MCP
  ``readOnlyHint`` annotation) and a risk label (from tool ``_meta``).
* ``session()`` - opens a Streamable-HTTP MCP session authenticated with a short-lived
  service token that pins the tenant and the allowed tools for one agent run.
* ``call()``    - executes a tool and returns a normalized outcome; never raises.

New tools added to the MCP server appear in the catalog automatically, but are
*disabled for every tenant* until an admin enables them (secure default).
"""

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from packages.core.security import create_service_token
from packages.schemas.api import CatalogTool

CATALOG_TTL_SECONDS = 60


@dataclass
class ToolOutcome:
    ok: bool
    data: Any = None
    error: str | None = None
    latency_ms: int = 0


class MCPUnavailableError(Exception):
    pass


class MCPGateway:
    def __init__(self, url: str):
        self.url = url
        self._catalog: list[CatalogTool] | None = None
        self._catalog_at = 0.0

    @asynccontextmanager
    async def session(self, token: str) -> AsyncIterator[Client]:
        timeout = httpx2.Timeout(30.0, read=60.0)
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as http:
            async with Client(streamable_http_client(self.url, http_client=http), read_timeout_seconds=60) as client:
                yield client

    async def catalog(self, force: bool = False) -> list[CatalogTool]:
        if not force and self._catalog is not None and time.monotonic() - self._catalog_at < CATALOG_TTL_SECONDS:
            return self._catalog
        token = create_service_token(tenant_id=None, run_id=None, user_id=None, allowed_tools=[], ttl_seconds=60)
        try:
            tools = []
            async with self.session(token) as client:
                cursor = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    tools.extend(page.tools)
                    cursor = page.next_cursor
                    if not cursor:
                        break
        except Exception as exc:  # ExceptionGroups from the transport, connection errors, 401s...
            raise MCPUnavailableError(f"MCP server unreachable at {self.url}: {type(exc).__name__}") from exc
        catalog = []
        for tool in tools:
            read_only = bool(tool.annotations and tool.annotations.read_only_hint)
            meta = tool.meta or {}
            catalog.append(
                CatalogTool(
                    name=tool.name,
                    title=tool.title or (tool.annotations.title if tool.annotations else None),
                    description=(tool.description or "").strip(),
                    input_schema=tool.input_schema,
                    read_only=read_only,
                    risk=str(meta.get("tenantpilot/risk") or ("read" if read_only else "write")),
                )
            )
        self._catalog, self._catalog_at = catalog, time.monotonic()
        return catalog

    @staticmethod
    async def call(client: Client, name: str, arguments: dict[str, Any], timeout_s: float = 45.0) -> ToolOutcome:
        started = time.perf_counter()
        try:
            result = await client.call_tool(name, arguments, read_timeout_seconds=timeout_s)
        except Exception as exc:
            return ToolOutcome(
                False,
                error=f"tool server error ({type(exc).__name__})",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        latency = int((time.perf_counter() - started) * 1000)
        text = "".join(getattr(c, "text", "") for c in (result.content or []))
        if result.is_error:
            # Strip the SDK's "Error executing tool x: " prefix; keep the controlled message.
            message = text.split(": ", 1)[1] if text.startswith("Error executing tool") and ": " in text else text
            return ToolOutcome(False, error=message or "tool failed", latency_ms=latency)
        data: Any = result.structured_content
        if data is None:
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"text": text}
        return ToolOutcome(True, data=data, latency_ms=latency)
