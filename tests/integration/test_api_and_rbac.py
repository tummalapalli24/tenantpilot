"""API + Postgres: onboarding flow and role-based access control."""

import pytest

from tests.conftest import MOCK_URL, auth

pytestmark = pytest.mark.integration


async def test_login_rejects_bad_credentials(api):
    resp = await api.post("/api/auth/login", json={"email": "admin@tenantpilot.dev", "password": "nope"})
    assert resp.status_code == 401
    resp = await api.post("/api/auth/login", json={"email": "ghost@tenantpilot.dev", "password": "nope"})
    assert resp.status_code == 401


async def test_requests_without_token_are_rejected_and_carry_request_id(api):
    resp = await api.get("/api/tenants")
    assert resp.status_code == 401
    assert resp.headers["x-request-id"] and resp.json()["request_id"] == resp.headers["x-request-id"]


async def test_admin_onboards_a_customer_in_one_call(api, tokens):
    resp = await api.post(
        "/api/tenants",
        headers=auth(tokens["admin"]),
        json={
            "name": "Globex Facilities",
            "slug": "globex",
            "api_base_url": f"{MOCK_URL}/acme/v1",
            "api_key": "acme-demo-key-123",
            "allowed_tools": ["search_knowledge", "get_customer"],
            "config": {"model_name": "offline-heuristic", "max_context_tokens": 5000},
        },
    )
    assert resp.status_code == 201, resp.text
    tenant = resp.json()
    assert tenant["config"]["model_name"] == "offline-heuristic"
    assert "Globex Facilities" in tenant["config"]["system_prompt"]

    connectors = (await api.get("/api/tenants/globex/connectors", headers=auth(tokens["admin"]))).json()
    assert connectors[0]["credentials_secret_ref"].startswith("db://")  # stored encrypted, only a ref returned
    assert "acme-demo-key-123" not in str(connectors)
    test = await api.post(f"/api/tenants/globex/connectors/{connectors[0]['id']}/test", headers=auth(tokens["admin"]))
    assert test.json()["status"] == "healthy"

    tools = {t["name"]: t for t in (await api.get("/api/tenants/globex/tools", headers=auth(tokens["admin"]))).json()}
    assert tools["get_customer"]["enabled"] and not tools["create_ticket"]["enabled"]
    assert tools["create_ticket"]["effective_requires_approval"]  # writes gated even before enabling

    dup = await api.post("/api/tenants", headers=auth(tokens["admin"]), json={"name": "Again", "slug": "globex"})
    assert dup.status_code == 409


async def test_rbac_matrix(api, tokens):
    new_tenant = {"name": "Nope Inc", "slug": "nope-inc"}
    assert (await api.post("/api/tenants", headers=auth(tokens["engineer"]), json=new_tenant)).status_code == 403
    # engineers configure workflows, viewers only read
    patch = {"config": {"reasoning_effort": "high"}}
    assert (await api.patch("/api/tenants/acme", headers=auth(tokens["engineer"]), json=patch)).status_code == 200
    assert (await api.patch("/api/tenants/acme", headers=auth(tokens["viewer"]), json=patch)).status_code == 403
    assert (
        await api.patch("/api/tenants/acme", headers=auth(tokens["engineer"]), json={"status": "suspended"})
    ).status_code == 403
    run = {"message": "What is the ticket priority policy?"}
    assert (await api.post("/api/agent/run", headers=auth(tokens["viewer"], "acme"), json=run)).status_code == 403
    assert (await api.get("/api/runs", headers=auth(tokens["viewer"], "acme"))).status_code == 200
    perms = {"permissions": [{"tool_name": "create_ticket", "enabled": True}]}
    assert (await api.post("/api/tenants/acme/tools", headers=auth(tokens["engineer"]), json=perms)).status_code == 403


async def test_config_changes_are_audited(api, tokens):
    await api.patch("/api/tenants/acme", headers=auth(tokens["engineer"]), json={"config": {"retrieval_top_k": 7}})
    logs = (await api.get("/api/tenants/acme/audit-logs", headers=auth(tokens["admin"]))).json()
    entry = next(entry for entry in logs if entry["action"] == "tenant.update")
    assert entry["actor_email"] == "engineer@tenantpilot.dev" and entry["details"]["retrieval_top_k"] == 7
    assert entry["request_id"]


async def test_unknown_model_and_tools_are_rejected(api, tokens):
    bad_model = await api.patch(
        "/api/tenants/acme", headers=auth(tokens["admin"]), json={"config": {"model_name": "gpt-banana"}}
    )
    assert bad_model.status_code == 422
    bad_tool = await api.post(
        "/api/tenants/acme/tools",
        headers=auth(tokens["admin"]),
        json={"permissions": [{"tool_name": "rm_rf", "enabled": True}]},
    )
    assert bad_tool.status_code == 422


async def test_health_and_readiness(api):
    assert (await api.get("/api/health")).json() == {"status": "ok"}
    ready = (await api.get("/api/ready")).json()
    assert ready["status"] == "ready" and ready["checks"] == {"database": "ok", "redis": "ok"}
