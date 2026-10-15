"""REST API connector: declarative operations, auth header, retries, response mapping.

Failure policy (this is what the "customer API returns 500" test exercises):
* 5xx, 429, timeouts, connection errors  -> retried with exponential backoff
* 404 (or configured not-found statuses) -> ConnectorError(kind="not_found"), no retry
* other 4xx                              -> ConnectorError, no retry (retrying won't help)
* retries exhausted                      -> ConnectorError(kind="unavailable") that says how
                                            many attempts were made
"""

import time
from typing import Any

import httpx

from packages.connectors.base import Connector, ConnectorError, HealthResult
from packages.connectors.mapping import get_path, map_fields, render_path, render_template
from packages.core.metrics import CONNECTOR_REQUESTS
from packages.core.retry import RetryExhaustedError, retry_async
from packages.schemas.connectors import RestConnectorConfig


class RestApiConnector(Connector):
    connector_type = "rest_api"

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str | None,
        config: RestConnectorConfig,
        client: httpx.AsyncClient | None = None,
    ):
        super().__init__(name)
        self.base_url = base_url.rstrip("/")
        self.config = config
        self._api_key = api_key
        self._client = client or httpx.AsyncClient(timeout=config.timeout_seconds)
        self._owns_client = client is None

    def supports(self, operation: str) -> bool:
        return operation in self.config.operations

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": "TenantPilot-Connector/1.0"}
        if self._api_key:
            prefix = f"{self.config.auth_scheme} " if self.config.auth_scheme else ""
            headers[self.config.auth_header] = prefix + self._api_key
        return headers

    async def health_check(self) -> HealthResult:
        started = time.perf_counter()
        try:
            resp = await self._client.get(self.base_url + self.config.health_path, headers=self._headers())
            latency = int((time.perf_counter() - started) * 1000)
            if resp.is_success:
                return HealthResult(True, latency, f"HTTP {resp.status_code}")
            return HealthResult(False, latency, f"HTTP {resp.status_code}")
        except httpx.HTTPError as exc:
            return HealthResult(False, int((time.perf_counter() - started) * 1000), type(exc).__name__)

    async def execute(self, operation: str, payload: dict[str, Any]) -> Any:
        spec = self.config.operations.get(operation)
        if spec is None:
            raise ConnectorError(f"{self.name} does not implement '{operation}'", kind="not_supported")
        url = self.base_url + render_path(spec.path, payload)
        params = {k: render_template(v, payload) for k, v in spec.query.items()}
        body = render_template(spec.body, payload) if spec.body is not None else None

        async def attempt() -> Any:
            try:
                resp = await self._client.request(
                    spec.method, url, params=params or None, json=body, headers=self._headers()
                )
            except httpx.TimeoutException as exc:
                CONNECTOR_REQUESTS.labels(self.connector_type, "timeout").inc()
                raise ConnectorError("request timed out", kind="unavailable", retryable=True) from exc
            except httpx.TransportError as exc:
                CONNECTOR_REQUESTS.labels(self.connector_type, "connection_error").inc()
                raise ConnectorError(
                    f"connection failed ({type(exc).__name__})", kind="unavailable", retryable=True
                ) from exc
            status = resp.status_code
            CONNECTOR_REQUESTS.labels(self.connector_type, str(status)).inc()
            if status in spec.not_found_statuses:
                raise ConnectorError("record not found", kind="not_found", status_code=status)
            if status == 429 or status >= 500:
                raise ConnectorError(f"HTTP {status}", kind="unavailable", retryable=True, status_code=status)
            if status in (401, 403):
                raise ConnectorError(
                    f"HTTP {status} - check connector credentials", kind="unauthorized", status_code=status
                )
            if status >= 400:
                raise ConnectorError(f"HTTP {status}", kind="bad_request", status_code=status)
            try:
                return resp.json()
            except ValueError as exc:
                raise ConnectorError("response was not valid JSON", kind="unavailable") from exc

        try:
            data = await retry_async(
                attempt,
                is_retryable=lambda e: isinstance(e, ConnectorError) and e.retryable,
                attempts=self.config.max_attempts,
            )
        except RetryExhaustedError as exc:
            last = exc.last_error
            raise ConnectorError(
                f"{self.name} unavailable after {exc.attempts} attempts ({last})",
                kind="unavailable",
                status_code=getattr(last, "status_code", None),
            ) from exc

        result = get_path(data, spec.result_path)
        if spec.list_result:
            return [map_fields(item, spec.field_map) for item in (result or [])]
        if result is None:
            raise ConnectorError("record not found", kind="not_found")
        return map_fields(result, spec.field_map)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
