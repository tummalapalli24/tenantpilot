"""PostgreSQL connector for customers whose system of record is a database.

The model never writes SQL. Each operation is a *named, parameterized, read-only*
query defined by an engineer in the connector config; the model only supplies argument
values, which are bound as parameters ($1, $2...). Defense in depth on top of that:
the session is ``default_transaction_read_only`` with a ``statement_timeout``, and the
database role the customer grants should itself be SELECT-only.
"""

import time
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg

from packages.connectors.base import Connector, ConnectorError, HealthResult
from packages.connectors.mapping import map_fields
from packages.core.metrics import CONNECTOR_REQUESTS
from packages.core.retry import RetryExhaustedError, retry_async
from packages.schemas.connectors import PostgresConnectorConfig

_TRANSIENT = (OSError, asyncpg.CannotConnectNowError, asyncpg.TooManyConnectionsError)


def build_dsn(base_url: str, password: str | None) -> str:
    """Insert the (secret) password into a display-safe DSN like postgresql://user@host/db."""
    parts = urlsplit(base_url)
    if not password:
        return base_url
    user = parts.username or ""
    host = parts.hostname or "localhost"
    netloc = f"{quote(user, safe='')}:{quote(password, safe='')}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class PostgresConnector(Connector):
    connector_type = "postgres"

    def __init__(self, name: str, dsn: str, config: PostgresConnectorConfig):
        super().__init__(name)
        self._dsn = dsn
        self.config = config

    def supports(self, operation: str) -> bool:
        return operation in self.config.operations

    async def _connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(
            self._dsn,
            timeout=5,
            server_settings={
                "statement_timeout": str(self.config.statement_timeout_ms),
                "default_transaction_read_only": "on",
                "application_name": "tenantpilot-connector",
            },
        )

    async def health_check(self) -> HealthResult:
        started = time.perf_counter()
        try:
            conn = await self._connect()
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
            return HealthResult(True, int((time.perf_counter() - started) * 1000), "SELECT 1 ok")
        except Exception as exc:  # health checks report, never raise
            return HealthResult(False, int((time.perf_counter() - started) * 1000), type(exc).__name__)

    async def execute(self, operation: str, payload: dict[str, Any]) -> Any:
        spec = self.config.operations.get(operation)
        if spec is None:
            raise ConnectorError(f"{self.name} does not implement '{operation}'", kind="not_supported")
        missing = [p for p in spec.params if payload.get(p) is None]
        if missing:
            raise ConnectorError(f"missing required value(s): {', '.join(missing)}", kind="bad_request")
        args = [payload[p] for p in spec.params]

        async def attempt() -> list[asyncpg.Record]:
            conn = await self._connect()
            try:
                async with conn.transaction(readonly=True):
                    return await conn.fetch(spec.sql, *args)
            finally:
                await conn.close()

        try:
            rows = await retry_async(attempt, is_retryable=lambda e: isinstance(e, _TRANSIENT), attempts=3)
            CONNECTOR_REQUESTS.labels(self.connector_type, "ok").inc()
        except RetryExhaustedError as exc:
            CONNECTOR_REQUESTS.labels(self.connector_type, "unavailable").inc()
            raise ConnectorError(
                f"{self.name} database unavailable after {exc.attempts} attempts", kind="unavailable"
            ) from exc
        except asyncpg.PostgresError as exc:
            CONNECTOR_REQUESTS.labels(self.connector_type, "query_error").inc()
            raise ConnectorError(f"query failed ({type(exc).__name__})", kind="bad_request") from exc

        records = [map_fields(dict(r), spec.field_map) for r in rows[: spec.max_rows]]
        if spec.list_result:
            return records
        if not records:
            raise ConnectorError("record not found", kind="not_found")
        return records[0]
