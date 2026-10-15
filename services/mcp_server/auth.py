"""Bearer-token verification for the MCP server (MCP SDK ``TokenVerifier`` protocol).

The SDK rejects requests without a valid token with HTTP 401 before any MCP message
is processed. Verified claims (tenant, run, allowed tools) are exposed to tools via
``get_access_token().claims``.
"""

import logging

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError

from packages.core.security import decode_service_token

log = logging.getLogger(__name__)


class ServiceTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = decode_service_token(token)
        except jwt.PyJWTError as exc:
            log.warning("rejected MCP service token", extra={"reason": type(exc).__name__})
            return None
        return AccessToken(
            token=token,
            client_id=claims.get("sub", "agent-runtime"),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=claims.get("exp"),
            subject=claims.get("user_id"),
            claims=claims,
        )


def caller_claims(tool_name: str) -> dict:
    """Authorize a tool invocation against the verified token. Raises ToolError, which
    the model sees as a normal tool error (is_error=True) with this message."""
    token = get_access_token()
    claims = (token.claims or {}) if token else {}
    if not claims:
        raise ToolError("unauthenticated tool call")
    if not claims.get("tenant_id"):
        raise ToolError("service token is not scoped to a tenant")
    if tool_name not in claims.get("allowed_tools", []):
        # Second, independent enforcement point: the agent runtime already filtered the
        # tool list, but the MCP server does not trust that it did.
        raise ToolError(f"tool '{tool_name}' is not enabled for this tenant")
    return claims
