"""Shared test setup.

Unit tests need nothing. Integration tests (``-m integration``) need Postgres+pgvector
and Redis (``docker compose up -d postgres redis`` locally; service containers in CI).
The ``infrastructure`` fixture then:

1. recreates a dedicated ``tenantpilot_test`` database (the dev database is untouched),
2. runs ``cli migrate`` + ``cli seed-demo`` in subprocesses (exactly what deploys run),
3. starts the MCP server and the mock customer API as real subprocesses on free ports.

Environment variables are set at import time, before any app module reads settings.
"""

import asyncio
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


PG_HOST = os.environ.get("TEST_PG_HOST", "localhost")
PG_PORT = os.environ.get("TEST_PG_PORT", "5432")
TEST_DB = os.environ.get("TEST_DB_NAME", "tenantpilot_test")
MCP_PORT = _free_port()
MOCK_PORT = _free_port()

os.environ.update(
    {
        "ENVIRONMENT": "test",
        "LOG_FORMAT": "text",
        "LOG_LEVEL": "WARNING",
        "DATABASE_URL": f"postgresql+asyncpg://tenantpilot_app:app-password@{PG_HOST}:{PG_PORT}/{TEST_DB}",
        "MIGRATIONS_DATABASE_URL": f"postgresql+asyncpg://tenantpilot:owner-password@{PG_HOST}:{PG_PORT}/{TEST_DB}",
        "APP_DB_PASSWORD": "app-password",
        "REDIS_URL": os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/15"),
        "INGEST_MODE": "inline",
        "MCP_SERVER_URL": f"http://127.0.0.1:{MCP_PORT}/mcp",
        "OBJECT_STORE_DIR": tempfile.mkdtemp(prefix="tp-test-uploads-"),
        "ANTHROPIC_API_KEY": "",
        "SEED_MODEL": "offline-heuristic",
        "AGENT_RATE_LIMIT_PER_MINUTE": "1000",
        "ACME_API_BASE_URL": f"http://127.0.0.1:{MOCK_PORT}/acme/v1",
        "BETACORP_CRM_DSN": f"postgresql://betacorp_reader@{PG_HOST}:{PG_PORT}/{TEST_DB}",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "",
    }
)

MOCK_ADMIN = {"X-Admin-Token": "mock-admin-token"}
MOCK_URL = f"http://127.0.0.1:{MOCK_PORT}"
DEMO_PASSWORD = "tenantpilot-demo"


def _postgres_available() -> bool:
    try:
        with socket.create_connection((PG_HOST, int(PG_PORT)), timeout=1):
            return True
    except OSError:
        return False


async def _recreate_database() -> None:
    import asyncpg

    conn = await asyncpg.connect(
        host=PG_HOST, port=int(PG_PORT), user="tenantpilot", password="owner-password", database="postgres"
    )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{TEST_DB}"')
    finally:
        await conn.close()


def _wait_http(url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"{url} did not become healthy")


@pytest.fixture(scope="session")
def infrastructure():
    if not _postgres_available():
        pytest.skip("Postgres not reachable - run `docker compose up -d postgres redis` for integration tests")
    asyncio.run(_recreate_database())
    env = dict(os.environ)
    for cmd in (["migrate"], ["seed-demo"]):
        subprocess.run([sys.executable, "-m", "apps.api.cli", *cmd], cwd=ROOT, env=env, check=True, capture_output=True)
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "services.mock_customer_api"],
            cwd=ROOT,
            env={**env, "PORT": str(MOCK_PORT), "HOST": "127.0.0.1"},
        ),
        subprocess.Popen(
            [sys.executable, "-m", "services.mcp_server"],
            cwd=ROOT,
            env={**env, "PORT": str(MCP_PORT), "HOST": "127.0.0.1"},
        ),
    ]
    try:
        _wait_http(f"{MOCK_URL}/health")
        _wait_http(f"http://127.0.0.1:{MCP_PORT}/ready")
        yield
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait(timeout=10)


@pytest.fixture(scope="session")
async def api(infrastructure):
    """The FastAPI app in-process (lifespan included), talking to the real subprocesses."""
    from apps.api.main import app

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=60) as client:
            yield client


@pytest.fixture(scope="session")
async def tokens(api) -> dict[str, str]:
    out = {}
    for email in (
        "admin@tenantpilot.dev",
        "engineer@tenantpilot.dev",
        "acme-engineer@tenantpilot.dev",
        "viewer@tenantpilot.dev",
    ):
        resp = await api.post("/api/auth/login", json={"email": email, "password": DEMO_PASSWORD})
        assert resp.status_code == 200, resp.text
        out[email.split("@")[0]] = resp.json()["access_token"]
    return out


def auth(token: str, tenant: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if tenant:
        headers["X-Tenant-ID"] = tenant
    return headers


@pytest.fixture
async def mock_api():
    """HTTP client for the mock customer system's admin endpoints; reset around each test."""
    async with httpx.AsyncClient(base_url=MOCK_URL, headers=MOCK_ADMIN) as client:
        await client.post("/_admin/reset")
        yield client
        await client.post("/_admin/reset")
