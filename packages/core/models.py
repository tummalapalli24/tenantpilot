"""SQLAlchemy ORM models.

Tables fall into two groups:

* Control plane (no RLS): ``users``, ``tenants``, ``tenant_memberships``.
* Tenant data (RLS enforced, see migrations/versions/0001_initial.py): everything with
  a ``tenant_id`` column. Access these only through ``tenant_session()``.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 384


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _tenant_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), index=True)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------- control plane


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20))  # admin | engineer | viewer
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(63), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TenantMembership(Base):
    """Which tenants a non-admin user may act on. Admins implicitly see every tenant."""

    __tablename__ = "tenant_memberships"
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )


# ---------------------------------------------------------------------------- tenant data (RLS)


class TenantConfig(Base):
    __tablename__ = "tenant_configs"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    model_name: Mapped[str] = mapped_column(String(100))
    temperature: Mapped[float] = mapped_column(Float, default=0.2)
    reasoning_effort: Mapped[str] = mapped_column(String(10), default="medium")
    max_context_tokens: Mapped[int] = mapped_column(Integer, default=6000)
    retrieval_top_k: Mapped[int] = mapped_column(Integer, default=6)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Secret(Base):
    """Connector credentials, encrypted at rest (Fernet). Referenced as ``db://<id>``."""

    __tablename__ = "secrets"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(200))
    ciphertext: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class Connector(Base):
    __tablename__ = "connectors"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    name: Mapped[str] = mapped_column(String(100))
    connector_type: Mapped[str] = mapped_column(String(30))  # rest_api | postgres
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    credentials_secret_ref: Mapped[str | None] = mapped_column(String(300), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # unknown | healthy | unhealthy
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    filename: Mapped[str] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(30), default="upload")  # upload | seed
    content_type: Mapped[str] = mapped_column(String(100), default="text/markdown")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|processing|indexed|failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        # Approximate nearest-neighbour index for cosine similarity search.
        Index(
            "ix_document_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", default=dict)
    created_at: Mapped[datetime] = _created()


class ToolPermission(Base):
    __tablename__ = "tool_permissions"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(String(100), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    request: Mapped[str] = mapped_column(Text)
    response: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    # running | awaiting_approval | completed | failed
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    model: Mapped[str] = mapped_column(String(100))
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Serialized transcript + pending tool calls, so a paused run can resume in another
    # process (e.g. a different API pod) after a human approves a write action.
    state: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = _created()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RunStep(Base):
    """One timed step of a run (context build, retrieval, LLM call, tool call, approval)."""

    __tablename__ = "run_steps"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    step_type: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="ok")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)


class ToolCall(Base):
    __tablename__ = "tool_calls"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    tool_use_id: Mapped[str] = mapped_column(String(100))
    source_id: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "T1", "T2"... cited by the model
    tool_name: Mapped[str] = mapped_column(String(100))
    request_json: Mapped[dict[str, Any]] = mapped_column(default=dict)
    response_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    # succeeded | failed | denied | invalid_arguments | pending_approval | rejected
    status: Mapped[str] = mapped_column(String(30))
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    approval_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # pending|approved|rejected
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = _uuid_pk()
    tenant_id: Mapped[uuid.UUID] = _tenant_fk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    action: Mapped[str] = mapped_column(String(100))
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = _created()


TENANT_SCOPED_TABLES = [
    "tenant_configs",
    "secrets",
    "connectors",
    "documents",
    "document_chunks",
    "tool_permissions",
    "agent_runs",
    "run_steps",
    "tool_calls",
    "audit_logs",
]
