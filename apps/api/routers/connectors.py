"""Customer system connectors. Credentials go in, are encrypted, and never come back out."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from apps.api.deps import TenantContext, tenant_scope
from packages.connectors.base import ConnectorError
from packages.connectors.factory import build_connector
from packages.core.audit import record_audit
from packages.core.db import tenant_session
from packages.core.models import Connector, utcnow
from packages.core.secrets import SecretResolutionError, resolve_secret, store_secret
from packages.schemas.api import ConnectorCreate, ConnectorOut, ConnectorTestResult
from packages.schemas.connectors import PostgresConnectorConfig, RestConnectorConfig

router = APIRouter(prefix="/api/tenants/{tenant_id}/connectors", tags=["connectors"])
_CONFIG_MODELS = {"rest_api": RestConnectorConfig, "postgres": PostgresConnectorConfig}


@router.post("", response_model=ConnectorOut, status_code=status.HTTP_201_CREATED)
async def create_connector(
    body: ConnectorCreate, ctx: TenantContext = Depends(tenant_scope("connectors:manage"))
) -> ConnectorOut:
    try:
        config = _CONFIG_MODELS[body.connector_type].model_validate(body.config).model_dump()
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid connector config: {exc}") from exc
    if not body.base_url:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "base_url is required")
    if body.credential and body.credentials_secret_ref:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "give either credential or credentials_secret_ref")
    if body.credentials_secret_ref and body.credentials_secret_ref.split("://")[0] not in ("env", "file", "db"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "secret refs must use env://, file:// or db://")
    try:
        async with tenant_session(ctx.tenant.id) as session:
            ref = body.credentials_secret_ref
            if body.credential:
                ref = await store_secret(session, ctx.tenant.id, f"{body.name} credential", body.credential)
            row = Connector(
                tenant_id=ctx.tenant.id,
                name=body.name,
                connector_type=body.connector_type,
                base_url=body.base_url.rstrip("/"),
                credentials_secret_ref=ref,
                config=config,
            )
            session.add(row)
            await session.flush()
            await record_audit(
                session,
                tenant_id=ctx.tenant.id,
                actor_id=ctx.principal.user_id,
                actor_email=ctx.principal.email,
                action="connector.create",
                resource_type="connector",
                resource_id=str(row.id),
                details={
                    "name": body.name,
                    "type": body.connector_type,
                    "base_url": row.base_url,
                    "operations": sorted(config["operations"]),
                },
            )
            return ConnectorOut.model_validate(row)
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, f"connector '{body.name}' already exists") from exc


@router.get("", response_model=list[ConnectorOut])
async def list_connectors(ctx: TenantContext = Depends(tenant_scope("tenants:read"))) -> list[ConnectorOut]:
    async with tenant_session(ctx.tenant.id) as session:
        rows = (await session.execute(select(Connector).order_by(Connector.created_at))).scalars()
        return [ConnectorOut.model_validate(r) for r in rows]


@router.post("/{connector_id}/test", response_model=ConnectorTestResult)
async def test_connector(
    connector_id: uuid.UUID, ctx: TenantContext = Depends(tenant_scope("config:write"))
) -> ConnectorTestResult:
    async with tenant_session(ctx.tenant.id) as session:
        row = await session.get(Connector, connector_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "connector not found")
        try:
            connector = build_connector(row, await resolve_secret(session, row.credentials_secret_ref))
        except (ConnectorError, SecretResolutionError) as exc:
            return ConnectorTestResult(status="unhealthy", latency_ms=0, detail=str(exc))
    try:
        health = await connector.health_check()
    finally:
        await connector.aclose()
    async with tenant_session(ctx.tenant.id) as session:
        row = await session.get(Connector, connector_id)
        row.status = "healthy" if health.healthy else "unhealthy"
        row.last_checked_at = utcnow()
    return ConnectorTestResult(
        status="healthy" if health.healthy else "unhealthy", latency_ms=health.latency_ms, detail=health.detail
    )
