"""Failure handling: customer API errors, revoked tools, missing LLM credentials, rate limits."""

import pytest
from redis.asyncio import Redis

from packages.core.config import get_settings
from packages.core.ratelimit import check_rate_limit
from tests.conftest import auth

pytestmark = pytest.mark.integration


async def test_customer_api_500_is_retried_then_returns_a_controlled_answer(api, tokens, mock_api):
    await mock_api.post("/_admin/failure", json={"mode": "http_500"})  # fail until reset
    resp = await api.post(
        "/api/agent/run",
        headers=auth(tokens["engineer"], "acme"),
        json={"message": "Customer C-102 says their sensor readings are wrong."},
    )
    body = resp.json()
    assert resp.status_code == 200 and body["status"] == "completed"  # controlled, not a 500
    assert body["answer"]["needs_human_followup"] and body["answer"]["confidence"] == "low"
    assert "unavailable after 3 attempts" in body["answer"]["summary"]
    stats = (await mock_api.get("/_admin/stats")).json()["stats"]
    assert stats["GET /acme/v1/customers/C-102"] == 3  # retried, then stopped at the limit

    trace = (await api.get(f"/api/runs/{body['run_id']}", headers=auth(tokens["engineer"], "acme"))).json()
    call = trace["tool_calls"][0]
    assert call["status"] == "failed" and "after 3 attempts" in call["error"]
    step = next(s for s in trace["steps"] if s["step_type"] == "tool_call")
    assert step["status"] == "error"


async def test_transient_failure_recovers_within_the_retry_budget(api, tokens, mock_api):
    await mock_api.post("/_admin/failure", json={"mode": "http_503", "count": 2})
    body = (
        await api.post(
            "/api/agent/run", headers=auth(tokens["engineer"], "acme"), json={"message": "Look up customer C-101"}
        )
    ).json()
    assert body["status"] == "completed"
    assert "Desert Bloom Cafe" in body["answer"]["summary"]


async def test_tool_revoked_while_awaiting_approval_is_not_executed(api, tokens, mock_api):
    admin = auth(tokens["admin"])
    created = (
        await api.post(
            "/api/agent/run",
            headers=auth(tokens["engineer"], "acme"),
            json={"message": "C-104 install failed - please open a ticket."},
        )
    ).json()
    assert created["status"] == "awaiting_approval"
    await api.post(
        "/api/tenants/acme/tools",
        headers=admin,
        json={"permissions": [{"tool_name": "create_ticket", "enabled": False}]},
    )
    try:
        pending = created["pending_actions"][0]
        resumed = (
            await api.post(
                f"/api/agent/{created['run_id']}/approve",
                headers=auth(tokens["engineer"], "acme"),
                json={"tool_call_id": pending["tool_call_id"], "decision": "approve"},
            )
        ).json()
        assert resumed["status"] == "completed"
        assert (await mock_api.get("/_admin/stats")).json()["tickets"] == []
        trace = (await api.get(f"/api/runs/{created['run_id']}", headers=auth(tokens["engineer"], "acme"))).json()
        call = next(c for c in trace["tool_calls"] if c["tool_name"] == "create_ticket")
        assert call["status"] == "denied"
    finally:
        await api.post(
            "/api/tenants/acme/tools",
            headers=admin,
            json={"permissions": [{"tool_name": "create_ticket", "enabled": True}]},
        )


async def test_claude_model_without_api_key_fails_with_a_clear_error(api, tokens):
    admin = auth(tokens["admin"])
    await api.patch("/api/tenants/acme", headers=admin, json={"config": {"model_name": "claude-opus-5"}})
    try:
        body = (
            await api.post(
                "/api/agent/run",
                headers=auth(tokens["engineer"], "acme"),
                json={"message": "What is the priority policy?"},
            )
        ).json()
        assert body["status"] == "failed" and "ANTHROPIC_API_KEY" in body["error"]
    finally:
        await api.patch("/api/tenants/acme", headers=admin, json={"config": {"model_name": "offline-heuristic"}})


async def test_suspended_tenant_cannot_run_agents(api, tokens):
    admin = auth(tokens["admin"])
    await api.patch("/api/tenants/betacorp", headers=admin, json={"status": "suspended"})
    try:
        resp = await api.post(
            "/api/agent/run", headers=auth(tokens["engineer"], "betacorp"), json={"message": "hello there"}
        )
        assert resp.status_code == 403 and "suspended" in resp.json()["detail"]
    finally:
        await api.patch("/api/tenants/betacorp", headers=admin, json={"status": "active"})


async def test_rate_limiter_blocks_after_limit():
    redis = Redis.from_url(get_settings().redis_url)
    try:
        key = "test:tenant-rl"
        results = [await check_rate_limit(redis, key, limit=2, window_s=60) for _ in range(3)]
        assert [r.allowed for r in results] == [True, True, False]
        assert results[-1].retry_after_s > 0 and results[-1].remaining == 0
    finally:
        await redis.aclose()
