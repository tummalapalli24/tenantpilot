"""Initial schema: control plane, tenant data with Row-Level Security, pgvector.

Revision ID: 0001_initial
Revises:
Create Date: 2026-09-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = [
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

# NULLIF(..., '') matters: once a transaction-local setting has been used on a pooled
# connection, current_setting() returns '' (not NULL) outside a tenant transaction, and
# ''::uuid would raise. With NULLIF the comparison is NULL -> no rows (fail closed).
TENANT_PREDICATE = "tenant_id = NULLIF(current_setting('app.current_tenant', true), '')::uuid"


def _uuid(**kw):
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, **kw)


def _tenant_fk(primary_key: bool = False):
    return sa.Column(
        "tenant_id",
        UUID(as_uuid=True),
        sa.ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=primary_key,
    )


def _ts(name: str, nullable: bool = False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------ control plane
    op.create_table(
        "users",
        _uuid(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        _ts("created_at"),
    )
    op.create_table(
        "tenants",
        _uuid(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(63), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
    )
    op.create_table(
        "tenant_memberships",
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tenant_id", UUID(as_uuid=True), sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                  primary_key=True),
    )

    # ------------------------------------------------------------------ tenant data
    op.create_table(
        "tenant_configs",
        _tenant_fk(primary_key=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(100), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("reasoning_effort", sa.String(10), nullable=False),
        sa.Column("max_context_tokens", sa.Integer(), nullable=False),
        sa.Column("retrieval_top_k", sa.Integer(), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        _ts("updated_at"),
    )
    op.create_table(
        "secrets",
        _uuid(),
        _tenant_fk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        _ts("created_at"),
    )
    op.create_table(
        "connectors",
        _uuid(),
        _tenant_fk(),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("connector_type", sa.String(30), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=True),
        sa.Column("credentials_secret_ref", sa.String(300), nullable=True),
        sa.Column("config", JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("last_checked_at", nullable=True),
        _ts("created_at"),
        sa.UniqueConstraint("tenant_id", "name"),
    )
    op.create_table(
        "documents",
        _uuid(),
        _tenant_fk(),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        _ts("created_at"),
        _ts("indexed_at", nullable=True),
    )
    op.create_table(
        "document_chunks",
        _uuid(),
        _tenant_fk(),
        sa.Column("document_id", UUID(as_uuid=True), sa.ForeignKey("documents.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("metadata", JSONB(), nullable=False),
        _ts("created_at"),
    )
    op.create_table(
        "tool_permissions",
        _tenant_fk(primary_key=True),
        sa.Column("tool_name", sa.String(100), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        _ts("updated_at"),
    )
    op.create_table(
        "agent_runs",
        _uuid(),
        _tenant_fk(),
        sa.Column("user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("request", sa.Text(), nullable=False),
        sa.Column("response", JSONB(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False),
        sa.Column("llm_calls", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("trace_id", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("state", JSONB(), nullable=True),
        _ts("created_at"),
        _ts("completed_at", nullable=True),
    )
    op.create_table(
        "run_steps",
        _uuid(),
        _tenant_fk(),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("started_at"),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("attributes", JSONB(), nullable=False),
    )
    op.create_table(
        "tool_calls",
        _uuid(),
        _tenant_fk(),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("tool_use_id", sa.String(100), nullable=False),
        sa.Column("source_id", sa.String(10), nullable=True),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("request_json", JSONB(), nullable=False),
        sa.Column("response_json", JSONB(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("approval_status", sa.String(20), nullable=True),
        sa.Column("approved_by", UUID(as_uuid=True), nullable=True),
        _ts("approved_at", nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at"),
    )
    op.create_table(
        "audit_logs",
        _uuid(),
        _tenant_fk(),
        sa.Column("actor_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_email", sa.String(320), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(50), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=True),
        sa.Column("details", JSONB(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        _ts("created_at"),
    )

    # ------------------------------------------------------------------ indexes
    for table in ["secrets", "connectors", "documents", "document_chunks", "agent_runs", "run_steps",
                  "tool_calls", "audit_logs"]:
        op.create_index(f"ix_{table}_tenant_id", table, ["tenant_id"])
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"])
    op.create_index("ix_run_steps_run_id", "run_steps", ["run_id"])
    op.create_index("ix_tool_calls_run_id", "tool_calls", ["run_id"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index(
        "ix_document_chunks_embedding_hnsw",
        "document_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    # ------------------------------------------------------------------ row-level security
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE applies the policy to the table owner too (defense in depth; the runtime
        # role is not the owner anyway).
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({TENANT_PREDICATE}) WITH CHECK ({TENANT_PREDICATE})"
        )


def downgrade() -> None:
    for table in reversed(TENANT_TABLES):
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    for table in ["audit_logs", "tool_calls", "run_steps", "agent_runs", "tool_permissions", "document_chunks",
                  "documents", "connectors", "secrets", "tenant_configs", "tenant_memberships", "tenants", "users"]:
        op.drop_table(table)
