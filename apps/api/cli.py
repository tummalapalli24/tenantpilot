"""Operational commands.

    python -m apps.api.cli migrate      # bootstrap DB roles/extension, alembic upgrade, grants
    python -m apps.api.cli seed-demo    # Acme + BetaCorp demo tenants, users, docs, connectors
    python -m apps.api.cli reindex --tenant acme   # re-chunk + re-embed after pipeline changes
    python -m apps.api.cli create-user --email a@b.c --password ... --role engineer --tenant acme

``migrate`` is idempotent and safe to run on every deploy (the Helm chart runs it in the
API pod's initContainer). It connects as the *owner* role and:
  1. creates/updates the least-privilege runtime role (NOSUPERUSER, NOBYPASSRLS),
  2. runs Alembic migrations (serialized by an advisory lock),
  3. grants the runtime role DML only - and makes audit_logs append-only.
"""

import argparse
import asyncio
import os
import re
import sys
import time
import uuid
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy import select

from packages.connectors.templates import BETACORP_POSTGRES_CRM, FIELD_SERVICE_REST_V1
from packages.core.config import get_settings
from packages.core.db import dispose_engine, platform_session, tenant_session
from packages.core.models import (
    Connector,
    Document,
    Tenant,
    TenantConfig,
    TenantMembership,
    ToolPermission,
    User,
)
from packages.core.secrets import store_secret
from packages.core.security import ROLES, hash_password
from packages.core.storage import get_object_store

ROOT = Path(__file__).resolve().parents[2]
_ROLE_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


def _ident(name: str) -> str:
    if not _ROLE_NAME.match(name):
        raise ValueError(f"unsafe role name: {name!r}")
    return f'"{name}"'


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _asyncpg_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _wait_for_database(timeout_s: float = 90) -> None:
    """On Kubernetes the Postgres pod may still be starting when this runs."""
    dsn = _asyncpg_dsn(get_settings().migrations_database_url)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            conn = await asyncpg.connect(dsn, timeout=5)
            await conn.close()
            return
        except (OSError, asyncpg.CannotConnectNowError, asyncpg.InvalidCatalogNameError) as exc:
            if time.monotonic() > deadline:
                raise RuntimeError(f"database not reachable after {timeout_s}s") from exc
            print(f"waiting for database ({type(exc).__name__})...", flush=True)
            await asyncio.sleep(2)


async def _bootstrap_roles() -> None:
    settings = get_settings()
    role = _ident(settings.app_db_role)
    await _wait_for_database()
    conn = await asyncpg.connect(_asyncpg_dsn(settings.migrations_database_url))
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", settings.app_db_role)
        verb = "ALTER" if exists else "CREATE"
        # DDL can't take bind parameters; the name is validated and the password quoted.
        await conn.execute(
            f"{verb} ROLE {role} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE "
            f"PASSWORD {_literal(settings.app_db_password)}"
        )
    finally:
        await conn.close()


async def _grant() -> None:
    settings = get_settings()
    role = _ident(settings.app_db_role)
    conn = await asyncpg.connect(_asyncpg_dsn(settings.migrations_database_url))
    try:
        db = await conn.fetchval("SELECT current_database()")
        await conn.execute(f'GRANT CONNECT ON DATABASE "{db}" TO {role}')
        await conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
        await conn.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}")
        await conn.execute(f"REVOKE ALL ON alembic_version FROM {role}")
        # Audit trail is append-only for the application, enforced by the database.
        await conn.execute(f"REVOKE UPDATE, DELETE ON audit_logs FROM {role}")
    finally:
        await conn.close()


def migrate() -> None:
    asyncio.run(_bootstrap_roles())
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    asyncio.run(_grant())
    print("migrations applied; runtime role granted least privilege")


# ------------------------------------------------------------------------------ seed data
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "tenantpilot-demo")
USERS = [
    ("admin@tenantpilot.dev", "admin", []),
    ("engineer@tenantpilot.dev", "engineer", ["acme", "betacorp"]),
    ("acme-engineer@tenantpilot.dev", "engineer", ["acme"]),
    ("viewer@tenantpilot.dev", "viewer", ["acme"]),
]
ACME_PROMPT = (
    "You support Acme Field Services' dispatch and field-engineering team (commercial HVAC installs and "
    "maintenance in southern Arizona). Follow Acme's runbooks and ticket priority policy. Treat anything "
    "involving refrigerant leaks, healthcare, schools or senior living as potentially critical."
)
BETACORP_PROMPT = (
    "You support BetaCorp Logistics' cold-chain account team. Answer from BetaCorp's SLA and account "
    "handbook. You cannot create tickets for BetaCorp; recommend who to contact instead."
)


async def _ensure_betacorp_crm(reader_password: str) -> None:
    """Simulates the customer's own database: a separate schema + a read-only role."""
    settings = get_settings()
    conn = await asyncpg.connect(_asyncpg_dsn(settings.migrations_database_url))
    try:
        await conn.execute("CREATE SCHEMA IF NOT EXISTS betacorp_crm")
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS betacorp_crm.accounts (
                account_ref text PRIMARY KEY, org_name text NOT NULL, lifecycle_state text NOT NULL,
                service_tier text, hq_region text, account_manager text, open_cases int DEFAULT 0, notes text)
        """)
        await conn.executemany(
            """INSERT INTO betacorp_crm.accounts VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
               ON CONFLICT (account_ref) DO NOTHING""",
            [
                (
                    "ACC-2041",
                    "Sonoran Fresh Produce",
                    "active",
                    "Gold",
                    "Phoenix, AZ",
                    "Nina Castillo",
                    1,
                    "Two reefer trailers flagged for temperature excursions in the last 7 days.",
                ),
                (
                    "ACC-2042",
                    "Canyon Pharma Distribution",
                    "active",
                    "Platinum",
                    "Tempe, AZ",
                    "Omar Haddad",
                    0,
                    "GDP-regulated pharmaceutical freight; excursions over 15 minutes require a deviation report.",
                ),
                (
                    "ACC-2043",
                    "Mesa Dairy Cooperative",
                    "at_risk",
                    "Silver",
                    "Mesa, AZ",
                    "Nina Castillo",
                    3,
                    "Renewal due 2026-11-01; three open service cases about late deliveries.",
                ),
            ],
        )
        exists = await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'betacorp_reader'")
        verb = "ALTER" if exists else "CREATE"
        await conn.execute(f"{verb} ROLE betacorp_reader LOGIN PASSWORD {_literal(reader_password)}")
        await conn.execute("GRANT USAGE ON SCHEMA betacorp_crm TO betacorp_reader")
        await conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA betacorp_crm TO betacorp_reader")
    finally:
        await conn.close()


async def _seed_tenant(slug: str, name: str, prompt: str, model: str, tools: dict[str, bool]) -> uuid.UUID:
    async with platform_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(name=name, slug=slug, status="active")
            session.add(tenant)
            await session.flush()
        tenant_id = tenant.id
    async with tenant_session(tenant_id) as session:
        if await session.get(TenantConfig, tenant_id) is None:
            session.add(
                TenantConfig(
                    tenant_id=tenant_id,
                    system_prompt=prompt,
                    model_name=model,
                    temperature=0.2,
                    reasoning_effort="medium",
                    max_context_tokens=6000,
                    retrieval_top_k=6,
                    approval_required=True,
                )
            )
        existing = {p.tool_name for p in (await session.execute(select(ToolPermission))).scalars()}
        for tool, enabled in tools.items():
            if tool not in existing:
                session.add(
                    ToolPermission(tenant_id=tenant_id, tool_name=tool, enabled=enabled, requires_approval=False)
                )
    return tenant_id


async def _seed_connector(
    tenant_id: uuid.UUID, name: str, ctype: str, base_url: str, credential: str, config: dict
) -> None:
    async with tenant_session(tenant_id) as session:
        existing = (await session.execute(select(Connector).where(Connector.name == name))).scalar_one_or_none()
        if existing is not None:
            # Converge to the desired state: the same seed works on a laptop (localhost URLs)
            # and in docker compose / Kubernetes (service DNS names).
            existing.base_url = base_url
            return
        ref = await store_secret(session, tenant_id, f"{name} credential", credential)
        session.add(
            Connector(
                tenant_id=tenant_id,
                name=name,
                connector_type=ctype,
                base_url=base_url,
                credentials_secret_ref=ref,
                config=config,
            )
        )


async def _seed_documents(tenant_id: uuid.UUID, folder: Path) -> int:
    from services.worker.ingest import ingest_document

    store = get_object_store()
    count = 0
    for path in sorted(folder.glob("*.md")):
        async with tenant_session(tenant_id) as session:
            doc = (await session.execute(select(Document).where(Document.filename == path.name))).scalar_one_or_none()
            if doc is not None and doc.status == "indexed":
                continue
            doc_id = doc.id if doc else uuid.uuid4()
            key = f"{tenant_id}/{doc_id}/{path.name}"
            if doc is None:
                session.add(
                    Document(
                        id=doc_id,
                        tenant_id=tenant_id,
                        filename=path.name,
                        source="seed",
                        content_type="text/markdown",
                        size_bytes=path.stat().st_size,
                        storage_key=key,
                        status="queued",
                    )
                )
        await store.put(key, path.read_bytes(), "text/markdown")
        await ingest_document(tenant_id, doc_id)  # inline: seeds must be queryable immediately
        count += 1
    return count


async def _seed() -> None:
    settings = get_settings()
    model = os.environ.get("SEED_MODEL") or (settings.default_model if settings.llm_available else "offline-heuristic")
    acme_url = os.environ.get("ACME_API_BASE_URL", "http://localhost:8081/acme/v1")
    acme_key = os.environ.get("ACME_API_KEY", "acme-demo-key-123")
    crm_dsn = os.environ.get("BETACORP_CRM_DSN", "postgresql://betacorp_reader@localhost:5432/tenantpilot")
    crm_password = os.environ.get("BETACORP_DB_PASSWORD", "betacorp-reader-pw")

    await _ensure_betacorp_crm(crm_password)
    acme = await _seed_tenant(
        "acme",
        "Acme Field Services",
        ACME_PROMPT,
        model,
        {"search_knowledge": True, "get_customer": True, "list_open_jobs": True, "create_ticket": True},
    )
    betacorp = await _seed_tenant(
        "betacorp",
        "BetaCorp Logistics",
        BETACORP_PROMPT,
        model,
        {"search_knowledge": True, "get_customer": True, "list_open_jobs": False, "create_ticket": False},
    )
    await _seed_connector(acme, "acme-field-api", "rest_api", acme_url, acme_key, FIELD_SERVICE_REST_V1)
    await _seed_connector(betacorp, "betacorp-crm", "postgres", crm_dsn, crm_password, BETACORP_POSTGRES_CRM)

    slugs = {"acme": acme, "betacorp": betacorp}
    async with platform_session() as session:
        for email, role, tenants in USERS:
            user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
            if user is None:
                user = User(email=email, password_hash=hash_password(DEMO_PASSWORD), role=role, is_active=True)
                session.add(user)
                await session.flush()
            for slug in tenants:
                if await session.get(TenantMembership, (user.id, slugs[slug])) is None:
                    session.add(TenantMembership(user_id=user.id, tenant_id=slugs[slug]))

    docs = await _seed_documents(acme, ROOT / "seed" / "acme") + await _seed_documents(
        betacorp, ROOT / "seed" / "betacorp"
    )
    print(f"seeded tenants acme/betacorp (model={model}), {len(USERS)} users, {docs} documents indexed")
    print(f"login: admin@tenantpilot.dev / {DEMO_PASSWORD}")


async def _reindex(slug: str) -> None:
    """Re-chunk and re-embed every document of a tenant (after changing the chunker or
    the embedding model - vectors from different models must never be mixed)."""
    from services.worker.ingest import ingest_document

    async with platform_session() as session:
        tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
    async with tenant_session(tenant.id) as session:
        doc_ids = [d.id for d in (await session.execute(select(Document))).scalars()]
    for doc_id in doc_ids:
        await ingest_document(tenant.id, doc_id)
    print(f"reindexed {len(doc_ids)} documents for {slug}")


async def _create_user(email: str, password: str, role: str, tenants: list[str]) -> None:
    async with platform_session() as session:
        user = User(email=email.lower(), password_hash=hash_password(password), role=role, is_active=True)
        session.add(user)
        await session.flush()
        for slug in tenants:
            tenant = (await session.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one()
            session.add(TenantMembership(user_id=user.id, tenant_id=tenant.id))
    print(f"created {role} {email}")


async def _run_and_dispose(coro) -> None:
    try:
        await coro
    finally:
        await dispose_engine()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="tenantpilot")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate")
    sub.add_parser("seed-demo")
    ri = sub.add_parser("reindex")
    ri.add_argument("--tenant", required=True)
    cu = sub.add_parser("create-user")
    cu.add_argument("--email", required=True)
    cu.add_argument("--password", required=True)
    cu.add_argument("--role", choices=ROLES, required=True)
    cu.add_argument("--tenant", action="append", default=[])
    args = parser.parse_args(argv)
    if args.cmd == "migrate":
        migrate()
    elif args.cmd == "seed-demo":
        asyncio.run(_run_and_dispose(_seed()))
    elif args.cmd == "reindex":
        asyncio.run(_run_and_dispose(_reindex(args.tenant)))
    elif args.cmd == "create-user":
        asyncio.run(_run_and_dispose(_create_user(args.email, args.password, args.role, args.tenant)))


if __name__ == "__main__":
    sys.exit(main())
