"""Declarative connector configuration (stored as JSON in ``connectors.config``).

An FDE onboards a new customer system by writing one of these configs - HTTP paths,
auth header, and a field map from the customer's JSON/columns to the canonical models -
instead of writing new integration code.
"""

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

OPERATIONS = ("get_customer", "list_open_jobs", "create_ticket")


class RestOperation(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH"] = "GET"
    path: str = Field(description="Path template, e.g. /customers/{customer_id}")
    query: dict[str, str] = Field(default_factory=dict, description="Query params; values may be templates")
    body: dict[str, Any] | None = Field(default=None, description="JSON body template for writes")
    result_path: str | None = Field(default=None, description="Dotted path to the record/list in the response")
    list_result: bool = False
    field_map: dict[str, str] = Field(default_factory=dict, description="canonical_field -> source path")
    not_found_statuses: list[int] = Field(default_factory=lambda: [404])


class RestConnectorConfig(BaseModel):
    auth_header: str = "X-API-Key"
    auth_scheme: str | None = Field(default=None, description="Prefix such as 'Bearer'")
    health_path: str = "/health"
    timeout_seconds: float = 5.0
    max_attempts: int = Field(default=3, ge=1, le=6)
    operations: dict[str, RestOperation]


_READ_ONLY_SQL = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


class PostgresOperation(BaseModel):
    sql: str = Field(description="Parameterized, read-only SQL using $1..$n placeholders")
    params: list[str] = Field(default_factory=list, description="Payload keys bound to $1..$n, in order")
    list_result: bool = False
    field_map: dict[str, str] = Field(default_factory=dict)
    max_rows: int = Field(default=50, ge=1, le=500)

    @field_validator("sql")
    @classmethod
    def _read_only(cls, value: str) -> str:
        if not _READ_ONLY_SQL.match(value) or ";" in value.strip().rstrip(";"):
            raise ValueError("only single SELECT/WITH statements are allowed")
        return value


class PostgresConnectorConfig(BaseModel):
    statement_timeout_ms: int = Field(default=3000, ge=100, le=30000)
    operations: dict[str, PostgresOperation]
