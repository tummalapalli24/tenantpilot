"""Build live Connector objects from a tenant's stored connector rows."""

import uuid

from sqlalchemy import select

from packages.connectors.base import Connector, ConnectorError
from packages.connectors.postgres import PostgresConnector, build_dsn
from packages.connectors.rest import RestApiConnector
from packages.core.db import tenant_session
from packages.core.models import Connector as ConnectorRow
from packages.core.secrets import SecretResolutionError, resolve_secret
from packages.schemas.connectors import PostgresConnectorConfig, RestConnectorConfig


def build_connector(row: ConnectorRow, credential: str | None) -> Connector:
    if row.connector_type == "rest_api":
        if not row.base_url:
            raise ConnectorError(f"connector {row.name} has no base_url", kind="config")
        return RestApiConnector(row.name, row.base_url, credential, RestConnectorConfig.model_validate(row.config))
    if row.connector_type == "postgres":
        if not row.base_url:
            raise ConnectorError(f"connector {row.name} has no DSN", kind="config")
        return PostgresConnector(
            row.name, build_dsn(row.base_url, credential), PostgresConnectorConfig.model_validate(row.config)
        )
    raise ConnectorError(f"unknown connector type '{row.connector_type}'", kind="config")


async def load_tenant_connectors(tenant_id: uuid.UUID) -> list[Connector]:
    """Resolve credentials and build every connector configured for a tenant (RLS-scoped)."""
    connectors: list[Connector] = []
    async with tenant_session(tenant_id) as session:
        rows = (await session.execute(select(ConnectorRow).order_by(ConnectorRow.created_at))).scalars().all()
        for row in rows:
            try:
                credential = await resolve_secret(session, row.credentials_secret_ref)
            except SecretResolutionError as exc:
                raise ConnectorError(f"connector {row.name}: {exc}", kind="config") from exc
            connectors.append(build_connector(row, credential))
    return connectors


def find_provider(connectors: list[Connector], operation: str) -> Connector | None:
    return next((c for c in connectors if c.supports(operation)), None)
