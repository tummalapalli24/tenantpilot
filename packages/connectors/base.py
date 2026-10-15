"""The common connector interface.

Customer-specific integration logic lives behind ``Connector`` so the agent runtime and
MCP tools never know whether a tenant's customer records come from a REST API, a
database, or something else. Tools ask for a canonical *operation* ("get_customer");
the tenant's connector config decides how that operation is fulfilled.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

ErrorKind = Literal["not_found", "bad_request", "unauthorized", "unavailable", "not_supported", "config"]


class ConnectorError(Exception):
    def __init__(self, message: str, *, kind: ErrorKind, retryable: bool = False, status_code: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code


@dataclass
class HealthResult:
    healthy: bool
    latency_ms: int
    detail: str


class Connector(ABC):
    connector_type: str

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def supports(self, operation: str) -> bool: ...

    @abstractmethod
    async def health_check(self) -> HealthResult: ...

    @abstractmethod
    async def execute(self, operation: str, payload: dict[str, Any]) -> Any: ...

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass
