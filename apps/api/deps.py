"""FastAPI dependencies: authentication, permission checks, tenant authorization."""

import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import select

from packages.core.db import platform_session
from packages.core.logging import tenant_id_var
from packages.core.models import Tenant
from packages.core.security import Principal, decode_access_token, has_permission
from services.agent.mcp_gateway import MCPGateway
from services.agent.runtime import AgentRuntime, TenantRef

_bearer = HTTPBearer(auto_error=False)


async def get_principal(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> Principal:
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing bearer token", headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        return decode_access_token(creds.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"}
        ) from exc


def require(permission: str):
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not has_permission(principal.role, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"role '{principal.role}' lacks permission '{permission}'")
        return principal

    return dependency


@dataclass
class TenantContext:
    tenant: TenantRef
    status: str
    principal: Principal


async def load_tenant(ref: str) -> Tenant | None:
    async with platform_session() as session:
        try:
            clause = Tenant.id == uuid.UUID(ref)
        except ValueError:
            clause = Tenant.slug == ref.lower()
        return (await session.execute(select(Tenant).where(clause))).scalar_one_or_none()


def tenant_scope(permission: str, *, require_active: bool = False):
    """Authorize access to the tenant resolved by RequestContextMiddleware.

    Non-admins get 403 whether the tenant is missing or merely not theirs, so tenant
    existence can't be probed by guessing ids/slugs."""

    async def dependency(request: Request, principal: Principal = Depends(require(permission))) -> TenantContext:
        ref = request.scope.get("state", {}).get("tenant_ref")
        if not ref:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "tenant not specified: use /api/tenants/{id}/... or the X-Tenant-ID header"
            )
        tenant = await load_tenant(ref)
        if tenant is None:
            if "*" in principal.tenant_ids:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "tenant not found")
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to this tenant")
        if not principal.can_access_tenant(tenant.id):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "no access to this tenant")
        if require_active and tenant.status != "active":
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"tenant is {tenant.status}")
        tenant_id_var.set(str(tenant.id))
        return TenantContext(TenantRef(tenant.id, tenant.slug, tenant.name), tenant.status, principal)

    return dependency


def get_runtime(request: Request) -> AgentRuntime:
    return request.app.state.runtime


def get_gateway(request: Request) -> MCPGateway:
    return request.app.state.gateway


def get_redis(request: Request) -> Redis:
    return request.app.state.redis
