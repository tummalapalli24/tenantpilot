"""Request/response models for the control-plane REST API (also drive the OpenAPI docs)."""

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.schemas.agent import FinalAnswer

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
Effort = Literal["low", "medium", "high"]


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------------------ auth
class LoginRequest(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    tenant_ids: list[str]


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - OAuth token type, not a secret
    user: UserOut


# ------------------------------------------------------------------------------ tenants
class TenantConfigIn(BaseModel):
    system_prompt: str | None = Field(default=None, max_length=8000)
    model_name: str | None = Field(default=None, max_length=100)
    temperature: float | None = Field(default=None, ge=0, le=1)
    reasoning_effort: Effort | None = None
    max_context_tokens: int | None = Field(default=None, ge=1000, le=100_000)
    retrieval_top_k: int | None = Field(default=None, ge=1, le=20)
    approval_required: bool | None = None


class TenantConfigOut(ORM):
    system_prompt: str
    model_name: str
    temperature: float
    reasoning_effort: str
    max_context_tokens: int
    retrieval_top_k: int
    approval_required: bool
    updated_at: datetime


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str = Field(min_length=2, max_length=63)
    config: TenantConfigIn = Field(default_factory=TenantConfigIn)
    # Optional one-step onboarding of the customer's REST API (standard field-service template)
    api_base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=500)
    allowed_tools: list[str] = Field(default_factory=list)

    @field_validator("slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        if not SLUG_RE.match(value):
            raise ValueError("slug must be lowercase letters, digits and dashes")
        return value

    @field_validator("api_base_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        if value and not re.match(r"^https?://", value):
            raise ValueError("api_base_url must start with http:// or https://")
        return value.rstrip("/") if value else value


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    status: Literal["active", "suspended"] | None = None
    config: TenantConfigIn | None = None


class TenantOut(ORM):
    id: uuid.UUID
    name: str
    slug: str
    status: str
    created_at: datetime
    config: TenantConfigOut | None = None


class TenantStats(BaseModel):
    documents: int
    indexed_documents: int
    chunks: int
    connectors: int
    enabled_tools: int
    runs: int
    runs_awaiting_approval: int


class TenantDetail(TenantOut):
    stats: TenantStats


# ------------------------------------------------------------------------------ connectors
class ConnectorCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    connector_type: Literal["rest_api", "postgres"]
    base_url: str | None = Field(default=None, max_length=500)
    credential: str | None = Field(default=None, max_length=2000, description="Stored encrypted; never returned")
    credentials_secret_ref: str | None = Field(default=None, max_length=300)
    config: dict[str, Any]


class ConnectorOut(ORM):
    id: uuid.UUID
    name: str
    connector_type: str
    base_url: str | None
    credentials_secret_ref: str | None
    config: dict[str, Any]
    status: str
    last_checked_at: datetime | None
    created_at: datetime


class ConnectorTestResult(BaseModel):
    status: Literal["healthy", "unhealthy"]
    latency_ms: int
    detail: str


# ------------------------------------------------------------------------------ documents
class DocumentOut(ORM):
    id: uuid.UUID
    filename: str
    source: str
    content_type: str
    size_bytes: int
    status: str
    error: str | None
    chunk_count: int
    created_at: datetime
    indexed_at: datetime | None


class KnowledgeSearchHit(BaseModel):
    chunk_id: str
    document: str
    section: str | None
    score: float
    content: str


# ------------------------------------------------------------------------------ tools
class CatalogTool(BaseModel):
    name: str
    title: str | None = None
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    risk: str


class TenantTool(CatalogTool):
    enabled: bool
    requires_approval: bool
    effective_requires_approval: bool


class ToolPermissionIn(BaseModel):
    tool_name: str = Field(max_length=100)
    enabled: bool
    requires_approval: bool = False


class ToolPermissionsUpdate(BaseModel):
    permissions: list[ToolPermissionIn]


# ------------------------------------------------------------------------------ agent
class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ApprovalRequest(BaseModel):
    tool_call_id: uuid.UUID
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1000)


class PendingAction(BaseModel):
    tool_call_id: uuid.UUID
    tool: str
    arguments: dict[str, Any]
    reason: str


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    llm_calls: int = 0


class AgentRunResponse(BaseModel):
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    answer: FinalAnswer | None = None
    pending_actions: list[PendingAction] = Field(default_factory=list)
    error: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    latency_ms: int | None = None
    model: str
    usage: Usage = Field(default_factory=Usage)


# ------------------------------------------------------------------------------ runs / traces
class RunSummary(ORM):
    id: uuid.UUID
    tenant_id: uuid.UUID
    user_id: uuid.UUID | None
    request: str
    status: str
    model: str
    latency_ms: int | None
    input_tokens: int
    output_tokens: int
    created_at: datetime


class RunStepOut(ORM):
    seq: int
    step_type: str
    name: str
    status: str
    started_at: datetime
    latency_ms: int
    attributes: dict[str, Any]


class ToolCallOut(ORM):
    id: uuid.UUID
    tool_use_id: str
    source_id: str | None
    tool_name: str
    request_json: dict[str, Any]
    response_json: dict[str, Any] | None
    status: str
    requires_approval: bool
    approval_status: str | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    latency_ms: int | None
    error: str | None
    created_at: datetime


class RunDetail(RunSummary):
    response: dict[str, Any] | None
    error: str | None
    request_id: str | None
    trace_id: str | None
    cache_read_tokens: int
    llm_calls: int
    completed_at: datetime | None
    steps: list[RunStepOut]
    tool_calls: list[ToolCallOut]


class AuditLogOut(ORM):
    id: uuid.UUID
    actor_email: str | None
    action: str
    resource_type: str
    resource_id: str | None
    details: dict[str, Any]
    request_id: str | None
    created_at: datetime


class ModelInfo(BaseModel):
    id: str
    provider: Literal["anthropic", "offline"]
    available: bool
    supports_temperature: bool
    supports_effort: bool
    note: str | None = None
