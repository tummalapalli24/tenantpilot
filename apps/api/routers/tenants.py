"""Tenant onboarding and configuration (the "Create Customer" flow)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from apps.api.deps import TenantContext, get_gateway, require, tenant_scope
from packages.connectors.templates import FIELD_SERVICE_REST_V1
from packages.core.audit import record_audit
from packages.core.config import get_settings
from packages.core.db import platform_session, tenant_session
from packages.core.models import (
    AgentRun,
    AuditLog,
    Connector,
    Document,
    DocumentChunk,
    Tenant,
    TenantConfig,
    ToolPermission,
)
from packages.core.secrets import store_secret
from packages.core.security import Principal, has_permission
from packages.schemas.api import (
    AuditLogOut,
    TenantConfigOut,
    TenantCreate,
    TenantDetail,
    TenantOut,
    TenantStats,
    TenantUpdate,
)
from services.agent.llm.models import SELECTABLE_MODELS
from services.agent.mcp_gateway import MCPGateway, MCPUnavailableError

router = APIRouter(prefix="/api/tenants", tags=["tenants"])

DEFAULT_SYSTEM_PROMPT = (
    "You support {name}'s field-operations team. Prefer {name}'s documented procedures over general "
    "advice, and call out anything that affects safety, regulated sites, or SLAs."
)


def _check_model(model: str) -> None:
    if model not in SELECTABLE_MODELS and not model.startswith("claude-"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unsupported model '{model}'")


def _tenant_out(tenant: Tenant, cfg: TenantConfig | None) -> TenantOut:
    return TenantOut(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        status=tenant.status,
        created_at=tenant.created_at,
        config=TenantConfigOut.model_validate(cfg) if cfg else None,
    )


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    principal: Principal = Depends(require("tenants:manage")),
    gateway: MCPGateway = Depends(get_gateway),
) -> TenantOut:
    settings = get_settings()
    model = body.config.model_name or settings.default_model
    _check_model(model)
    if body.allowed_tools:
        try:
            catalog = {t.name for t in await gateway.catalog()}
        except MCPUnavailableError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "cannot validate tools: MCP unreachable") from exc
        unknown = sorted(set(body.allowed_tools) - catalog)
        if unknown:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"unknown tools: {', '.join(unknown)}")
    if body.api_base_url and not body.api_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "api_key is required with api_base_url")

    cfg_in = body.config
    try:
        # One transaction: the tenant row, then (after scoping the transaction to the new
        # tenant for RLS) its config, connector, secret, permissions and audit entry.
        async with platform_session() as session:
            tenant = Tenant(name=body.name.strip(), slug=body.slug, status="active")
            session.add(tenant)
            await session.flush()
            await session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": str(tenant.id)})
            cfg = TenantConfig(
                tenant_id=tenant.id,
                system_prompt=cfg_in.system_prompt
                if cfg_in.system_prompt is not None
                else DEFAULT_SYSTEM_PROMPT.format(name=tenant.name),
                model_name=model,
                temperature=cfg_in.temperature if cfg_in.temperature is not None else 0.2,
                reasoning_effort=cfg_in.reasoning_effort or "medium",
                max_context_tokens=cfg_in.max_context_tokens or 6000,
                retrieval_top_k=cfg_in.retrieval_top_k or 6,
                approval_required=True if cfg_in.approval_required is None else cfg_in.approval_required,
            )
            session.add(cfg)
            if body.api_base_url:
                ref = await store_secret(session, tenant.id, "primary-api credential", body.api_key or "")
                session.add(
                    Connector(
                        tenant_id=tenant.id,
                        name="primary-api",
                        connector_type="rest_api",
                        base_url=body.api_base_url,
                        credentials_secret_ref=ref,
                        config=FIELD_SERVICE_REST_V1,
                    )
                )
            for tool in sorted(set(body.allowed_tools)):
                session.add(ToolPermission(tenant_id=tenant.id, tool_name=tool, enabled=True, requires_approval=False))
            await record_audit(
                session,
                tenant_id=tenant.id,
                actor_id=principal.user_id,
                actor_email=principal.email,
                action="tenant.create",
                resource_type="tenant",
                resource_id=str(tenant.id),
                details={
                    "name": tenant.name,
                    "slug": tenant.slug,
                    "model": model,
                    "connector": bool(body.api_base_url),
                    "allowed_tools": body.allowed_tools,
                },
            )
            await session.flush()
            return _tenant_out(tenant, cfg)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"slug '{body.slug}' is already taken") from exc


@router.get("", response_model=list[TenantOut])
async def list_tenants(principal: Principal = Depends(require("tenants:read"))) -> list[TenantOut]:
    async with platform_session() as session:
        query = select(Tenant).order_by(Tenant.created_at)
        if "*" not in principal.tenant_ids:
            query = query.where(Tenant.id.in_([uuid.UUID(t) for t in principal.tenant_ids] or [uuid.uuid4()]))
        tenants = (await session.execute(query)).scalars().all()
    out = []
    for tenant in tenants:
        async with tenant_session(tenant.id) as session:
            out.append(_tenant_out(tenant, await session.get(TenantConfig, tenant.id)))
    return out


@router.get("/{tenant_id}", response_model=TenantDetail)
async def get_tenant(ctx: TenantContext = Depends(tenant_scope("tenants:read"))) -> TenantDetail:
    tid = ctx.tenant.id
    async with platform_session() as session:
        tenant = await session.get(Tenant, tid)
    async with tenant_session(tid) as session:
        cfg = await session.get(TenantConfig, tid)

        async def count(model, *where) -> int:
            return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)

        stats = TenantStats(
            documents=await count(Document),
            indexed_documents=await count(Document, Document.status == "indexed"),
            chunks=await count(DocumentChunk),
            connectors=await count(Connector),
            enabled_tools=await count(ToolPermission, ToolPermission.enabled.is_(True)),
            runs=await count(AgentRun),
            runs_awaiting_approval=await count(AgentRun, AgentRun.status == "awaiting_approval"),
        )
    return TenantDetail(**_tenant_out(tenant, cfg).model_dump(), stats=stats)


@router.patch("/{tenant_id}", response_model=TenantOut)
async def update_tenant(body: TenantUpdate, ctx: TenantContext = Depends(tenant_scope("tenants:read"))) -> TenantOut:
    principal = ctx.principal
    if (body.name is not None or body.status is not None) and not has_permission(principal.role, "tenants:manage"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "changing name/status requires tenants:manage")
    if body.config is not None and not has_permission(principal.role, "config:write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "changing agent configuration requires config:write")
    changes: dict = {}
    async with platform_session() as session:
        tenant = await session.get(Tenant, ctx.tenant.id)
        if body.name is not None and body.name != tenant.name:
            changes["name"] = body.name
            tenant.name = body.name
        if body.status is not None and body.status != tenant.status:
            changes["status"] = body.status
            tenant.status = body.status
    async with tenant_session(ctx.tenant.id) as session:
        cfg = await session.get(TenantConfig, ctx.tenant.id)
        if body.config is not None:
            updates = body.config.model_dump(exclude_none=True)
            if "model_name" in updates:
                _check_model(updates["model_name"])
            for key, value in updates.items():
                if getattr(cfg, key) != value:
                    changes[key] = value if key != "system_prompt" else f"<{len(value)} chars>"
                    setattr(cfg, key, value)
        if changes:
            await record_audit(
                session,
                tenant_id=ctx.tenant.id,
                actor_id=principal.user_id,
                actor_email=principal.email,
                action="tenant.update",
                resource_type="tenant",
                resource_id=str(ctx.tenant.id),
                details=changes,
            )
        await session.flush()
        return _tenant_out(tenant, cfg)


@router.get("/{tenant_id}/audit-logs", response_model=list[AuditLogOut])
async def audit_logs(
    ctx: TenantContext = Depends(tenant_scope("audit:read")),
    limit: int = Query(50, ge=1, le=200),
) -> list[AuditLogOut]:
    async with tenant_session(ctx.tenant.id) as session:
        rows = (await session.execute(select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit))).scalars()
        return [AuditLogOut.model_validate(r) for r in rows]
