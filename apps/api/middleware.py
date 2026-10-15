"""Request context middleware (pure ASGI, so it adds ~no overhead).

* Assigns every request an id (or accepts a well-formed incoming ``X-Request-ID``),
  exposes it to logs/traces via a contextvar and echoes it in the response.
* Resolves the *requested* tenant from the URL (``/api/tenants/{ref}/...``) or the
  ``X-Tenant-ID`` header into ``scope["state"]["tenant_ref"]``. It only resolves;
  authorization (does this user have access to that tenant?) happens in the
  ``tenant_scope`` dependency, which has the authenticated principal.
* Writes one structured access-log line per request.
"""

import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from packages.core.logging import request_id_var, run_id_var, tenant_id_var

log = logging.getLogger("tenantpilot.access")
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
_TENANT_PATH = re.compile(r"^/api/tenants/([^/]+)")
QUIET_PATHS = {"/api/health", "/api/ready", "/metrics"}


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode().lower(): v.decode(errors="replace") for k, v in scope.get("headers", [])}
        incoming = headers.get("x-request-id", "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        path_match = _TENANT_PATH.match(scope.get("path", ""))
        tenant_ref = path_match.group(1) if path_match else headers.get("x-tenant-id")
        scope.setdefault("state", {})["tenant_ref"] = tenant_ref
        scope["state"]["request_id"] = request_id

        tokens = (request_id_var.set(request_id), tenant_id_var.set(None), run_id_var.set(None))
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope.get("path") not in QUIET_PATHS:
                log.info(
                    "%s %s -> %s",
                    scope.get("method"),
                    scope.get("path"),
                    status_code,
                    extra={"status": status_code, "duration_ms": int((time.perf_counter() - started) * 1000)},
                )
            request_id_var.reset(tokens[0])
            tenant_id_var.reset(tokens[1])
            run_id_var.reset(tokens[2])
