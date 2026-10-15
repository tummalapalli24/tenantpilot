"""TenantPilot customer-operations MCP server.

Exposes four tools over MCP (Streamable HTTP). The agent runtime *discovers* them via
``tools/list`` - name, description, JSON input schema, output schema, and annotations
(``readOnlyHint`` tells the control plane which tools mutate customer systems) - instead
of hard-coding tool implementations into the agent.

Every call is tenant-scoped by the verified service token. Business data comes from the
tenant's configured connectors (REST API, Postgres, knowledge base), mapped into the
canonical models in packages/schemas/canonical.py.
"""

import logging
import uuid
from typing import Annotated, Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import Field, ValidationError
from sqlalchemy import text
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from packages.connectors.base import ConnectorError
from packages.connectors.factory import find_provider, load_tenant_connectors
from packages.connectors.knowledge import KnowledgeConnector
from packages.core.config import get_settings
from packages.core.db import platform_session
from packages.core.logging import configure_logging, run_id_var, tenant_id_var
from packages.core.telemetry import setup_telemetry
from packages.schemas.canonical import (
    Customer,
    Job,
    JobList,
    KnowledgeHit,
    KnowledgeSearchResult,
    Priority,
    Ticket,
)
from services.mcp_server.auth import ServiceTokenVerifier, caller_claims

log = logging.getLogger(__name__)
settings = get_settings()

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

mcp = MCPServer(
    "tenantpilot-customer-ops",
    title="TenantPilot Customer Operations",
    version="1.0.0",
    instructions="Customer-operations tools. Every call is scoped to the tenant named in the caller's token.",
    token_verifier=ServiceTokenVerifier(),
    auth=AuthSettings(
        issuer_url=settings.public_api_url,
        resource_server_url=settings.mcp_server_url,
        validate_token_resource=False,  # our verifier checks the JWT audience itself
        required_scopes=["mcp:tools"],
    ),
)


def _bind_context(claims: dict) -> uuid.UUID:
    tenant_id_var.set(claims["tenant_id"])
    run_id_var.set(claims.get("run_id"))
    return uuid.UUID(claims["tenant_id"])


def _controlled_error(exc: ConnectorError, what: str) -> ToolError:
    """Translate connector failures into messages the model (and operator) can act on."""
    if exc.kind == "not_found":
        return ToolError(f"No {what} found.")
    if exc.kind == "unavailable":
        return ToolError(f"The customer's system is unavailable ({exc}). Try again later or escalate to on-call.")
    if exc.kind == "unauthorized":
        return ToolError("The customer's system rejected TenantPilot's credentials; an admin must update them.")
    return ToolError(str(exc))


async def _run_operation(tenant_id: uuid.UUID, operation: str, payload: dict[str, Any], what: str) -> tuple[str, Any]:
    try:
        connectors = await load_tenant_connectors(tenant_id)
    except ConnectorError as exc:
        raise ToolError(f"Connector misconfigured: {exc}") from exc
    try:
        provider = find_provider(connectors, operation)
        if provider is None:
            raise ToolError(f"No connector is configured to provide '{operation}' for this tenant.")
        return provider.name, await provider.execute(operation, payload)
    except ConnectorError as exc:
        raise _controlled_error(exc, what) from exc
    finally:
        for connector in connectors:
            await connector.aclose()


def _validated(model: type, data: dict[str, Any], source: str):
    try:
        return model.model_validate({**data, "source": source} if "source" in model.model_fields else data)
    except ValidationError as exc:
        # A mapping bug in the connector config, not a model error - say so explicitly.
        fields = ", ".join(str(e["loc"][0]) for e in exc.errors()[:5])
        raise ToolError(
            f"The customer record did not match the expected schema (check field_map for: {fields})"
        ) from exc


@mcp.tool(title="Search knowledge base", annotations=READ, meta={"tenantpilot/risk": "read"})
async def search_knowledge(
    query: Annotated[str, Field(description="What to look up, e.g. an error code or procedure", max_length=500)],
    top_k: Annotated[int, Field(ge=1, le=10, description="Number of excerpts to return")] = 5,
) -> KnowledgeSearchResult:
    """Search the tenant's indexed knowledge base (runbooks, policies, manuals) and return
    the most relevant excerpts with their document and section."""
    tenant_id = _bind_context(caller_claims("search_knowledge"))
    try:
        hits = await KnowledgeConnector(tenant_id).execute("search_knowledge", {"query": query, "top_k": top_k})
    except ConnectorError as exc:
        raise ToolError(str(exc)) from exc
    return KnowledgeSearchResult(query=query, hits=[KnowledgeHit(**h) for h in hits])


@mcp.tool(title="Get customer", annotations=READ, meta={"tenantpilot/risk": "read"})
async def get_customer(
    customer_id: Annotated[str, Field(description="Customer identifier, e.g. C-104", max_length=40)],
) -> Customer:
    """Fetch one customer's account record (status, plan/tier, region, contact, notes)
    from the customer's system of record."""
    tenant_id = _bind_context(caller_claims("get_customer"))
    cid = customer_id.strip().upper()
    source, record = await _run_operation(tenant_id, "get_customer", {"customer_id": cid}, f"customer {cid}")
    return _validated(Customer, record, source)


@mcp.tool(title="List open jobs", annotations=READ, meta={"tenantpilot/risk": "read"})
async def list_open_jobs(
    customer_id: Annotated[str, Field(description="Customer identifier, e.g. C-104", max_length=40)],
    limit: Annotated[int, Field(ge=1, le=50)] = 20,
) -> JobList:
    """List a customer's open jobs / work orders (scheduled, in progress, failed or on
    hold), including technician notes and fault codes."""
    tenant_id = _bind_context(caller_claims("list_open_jobs"))
    cid = customer_id.strip().upper()
    source, records = await _run_operation(tenant_id, "list_open_jobs", {"customer_id": cid}, f"customer {cid}")
    jobs = [_validated(Job, r, source) for r in records]
    return JobList(customer_id=cid, jobs=jobs[:limit], total=len(jobs), truncated=len(jobs) > limit, source=source)


@mcp.tool(title="Create support ticket", annotations=WRITE, meta={"tenantpilot/risk": "write"})
async def create_ticket(
    customer_id: Annotated[str, Field(description="Customer identifier, e.g. C-104", max_length=40)],
    summary: Annotated[str, Field(min_length=10, max_length=500, description="What happened and what is needed")],
    priority: Annotated[Priority, Field(description="low | medium | high | critical, per the priority policy")],
) -> Ticket:
    """Create a support ticket in the customer's ticketing system. This changes the
    customer's system, so TenantPilot requires human approval before it runs."""
    tenant_id = _bind_context(caller_claims("create_ticket"))
    cid = customer_id.strip().upper()
    payload = {"customer_id": cid, "summary": summary.strip(), "priority": priority}
    source, record = await _run_operation(tenant_id, "create_ticket", payload, f"customer {cid}")
    log.info("ticket created", extra={"customer_id": cid, "priority": priority})
    return _validated(Ticket, record, source)


@mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/ready", methods=["GET"], include_in_schema=False)
async def ready(_: Request) -> Response:
    try:
        async with platform_session() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # readiness reports, never raises
        return JSONResponse({"status": "not_ready", "database": type(exc).__name__}, status_code=503)
    return JSONResponse({"status": "ready"})


@mcp.custom_route("/metrics", methods=["GET"], include_in_schema=False)
async def metrics(_: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def create_app():
    configure_logging("tenantpilot-mcp", settings.log_level, settings.log_format)
    setup_telemetry("tenantpilot-mcp")
    return mcp.streamable_http_app(
        # Stateless + JSON responses: any replica can serve any request, which is what
        # lets the Deployment scale horizontally behind a plain Kubernetes Service.
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=settings.mcp_allowed_hosts
        ),
        host="0.0.0.0",
    )
