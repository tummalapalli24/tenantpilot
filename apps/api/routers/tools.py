"""Tool governance: the discovered MCP catalog and per-tenant permissions."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from apps.api.deps import TenantContext, get_gateway, require, tenant_scope
from packages.core.audit import record_audit
from packages.core.db import tenant_session
from packages.core.models import TenantConfig, ToolPermission
from packages.core.security import Principal
from packages.schemas.api import CatalogTool, TenantTool, ToolPermissionsUpdate
from services.agent.mcp_gateway import MCPGateway, MCPUnavailableError
from services.agent.policy import resolve_policies

router = APIRouter(tags=["tools"])


async def _catalog(gateway: MCPGateway, force: bool = False) -> list[CatalogTool]:
    try:
        return await gateway.catalog(force=force)
    except MCPUnavailableError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc


async def _tenant_tools(ctx: TenantContext, catalog: list[CatalogTool]) -> list[TenantTool]:
    async with tenant_session(ctx.tenant.id) as session:
        perms = {p.tool_name: p for p in (await session.execute(select(ToolPermission))).scalars()}
        cfg = await session.get(TenantConfig, ctx.tenant.id)
    policies = resolve_policies(catalog, perms, cfg.approval_required if cfg else True)
    return [
        TenantTool(
            **tool.model_dump(),
            enabled=policies[tool.name].enabled,
            requires_approval=bool(perms.get(tool.name) and perms[tool.name].requires_approval),
            effective_requires_approval=policies[tool.name].requires_approval,
        )
        for tool in catalog
    ]


@router.get("/api/tools/catalog", response_model=list[CatalogTool])
async def tool_catalog(
    refresh: bool = Query(False),
    _: Principal = Depends(require("tenants:read")),
    gateway: MCPGateway = Depends(get_gateway),
) -> list[CatalogTool]:
    """Tools discovered from the MCP server (tools/list), normalized."""
    return await _catalog(gateway, force=refresh)


@router.get("/api/tenants/{tenant_id}/tools", response_model=list[TenantTool])
async def tenant_tools(
    ctx: TenantContext = Depends(tenant_scope("tenants:read")), gateway: MCPGateway = Depends(get_gateway)
) -> list[TenantTool]:
    return await _tenant_tools(ctx, await _catalog(gateway))


@router.post("/api/tenants/{tenant_id}/tools", response_model=list[TenantTool])
async def set_tool_permissions(
    body: ToolPermissionsUpdate,
    ctx: TenantContext = Depends(tenant_scope("tools:manage")),
    gateway: MCPGateway = Depends(get_gateway),
) -> list[TenantTool]:
    catalog = await _catalog(gateway)
    known = {t.name for t in catalog}
    unknown = sorted({p.tool_name for p in body.permissions} - known)
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown tools: {', '.join(unknown)}")
    async with tenant_session(ctx.tenant.id) as session:
        existing = {p.tool_name: p for p in (await session.execute(select(ToolPermission))).scalars()}
        changes = {}
        for perm in body.permissions:
            row = existing.get(perm.tool_name)
            if row is None:
                row = ToolPermission(tenant_id=ctx.tenant.id, tool_name=perm.tool_name)
                session.add(row)
            if row.enabled != perm.enabled or row.requires_approval != perm.requires_approval:
                changes[perm.tool_name] = {"enabled": perm.enabled, "requires_approval": perm.requires_approval}
            row.enabled, row.requires_approval = perm.enabled, perm.requires_approval
        if changes:
            await record_audit(
                session,
                tenant_id=ctx.tenant.id,
                actor_id=ctx.principal.user_id,
                actor_email=ctx.principal.email,
                action="tools.update",
                resource_type="tool_permission",
                details=changes,
            )
    return await _tenant_tools(ctx, catalog)
