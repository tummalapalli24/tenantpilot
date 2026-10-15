"""Agent + MCP end to end: tool use, traces, the approval workflow, and its race conditions."""

import asyncio

import pytest

from tests.conftest import auth

pytestmark = pytest.mark.integration

DIAGNOSE = "Customer C-104 says their installation failed. What happened and what should we do?"
TICKET = "Old Pueblo Hotel (C-106) reports a compressor alarm. Please open a ticket."


async def run(api, token, tenant, message):
    resp = await api.post("/api/agent/run", headers=auth(token, tenant), json={"message": message})
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_diagnosis_uses_tools_retrieval_and_produces_a_grounded_answer(api, tokens):
    result = await run(api, tokens["engineer"], "acme", DIAGNOSE)
    assert result["status"] == "completed" and result["pending_actions"] == []
    answer = result["answer"]
    text = (answer["summary"] + " ".join(f["statement"] for f in answer["findings"])).lower()
    assert "j-774" in text and "activation token" in text
    assert any("provisioning" in a.lower() for a in answer["recommended_actions"])

    trace = (await api.get(f"/api/runs/{result['run_id']}", headers=auth(tokens["engineer"], "acme"))).json()
    step_types = [s["step_type"] for s in trace["steps"]]
    assert step_types[:3] == ["tool_discovery", "retrieval", "context_build"]
    assert "llm_call" in step_types and step_types[-1] == "final_answer"
    tools_called = [c["tool_name"] for c in trace["tool_calls"]]
    assert tools_called[:2] == ["get_customer", "list_open_jobs"]
    assert all(c["status"] == "succeeded" for c in trace["tool_calls"])
    context = next(s for s in trace["steps"] if s["step_type"] == "context_build")["attributes"]
    assert context["selected"] and context["tokens"]["knowledge"] <= context["budget"]["knowledge"]
    final = next(s for s in trace["steps"] if s["step_type"] == "final_answer")["attributes"]
    assert final["schema_valid"] and final["unsupported_source_ids"] == []
    assert trace["request_id"] and trace["tenant_id"] and trace["llm_calls"] >= 3


async def test_write_action_waits_for_approval_then_executes_once(api, tokens, mock_api):
    result = await run(api, tokens["engineer"], "acme", TICKET)
    assert result["status"] == "awaiting_approval"
    pending = result["pending_actions"][0]
    assert pending["tool"] == "create_ticket" and pending["arguments"]["priority"] == "critical"
    assert (await mock_api.get("/_admin/stats")).json()["tickets"] == []  # nothing written yet

    decision = {"tool_call_id": pending["tool_call_id"], "decision": "approve", "comment": "on-call confirmed"}
    approved = await api.post(
        f"/api/agent/{result['run_id']}/approve", headers=auth(tokens["engineer"], "acme"), json=decision
    )
    assert approved.status_code == 200 and approved.json()["status"] == "completed"
    tickets = (await mock_api.get("/_admin/stats")).json()["tickets"]
    assert len(tickets) == 1 and tickets[0]["severity"] == "critical" and tickets[0]["customer_ref"] == "C-106"
    assert tickets[0]["ticket_no"] in approved.json()["answer"]["summary"]

    again = await api.post(
        f"/api/agent/{result['run_id']}/approve", headers=auth(tokens["engineer"], "acme"), json=decision
    )
    assert again.status_code == 409
    assert len((await mock_api.get("/_admin/stats")).json()["tickets"]) == 1

    trace = (await api.get(f"/api/runs/{result['run_id']}", headers=auth(tokens["engineer"], "acme"))).json()
    call = next(c for c in trace["tool_calls"] if c["tool_name"] == "create_ticket")
    assert call["approval_status"] == "approved" and call["approved_by"] and call["status"] == "succeeded"
    assert any(s["step_type"] == "approval" for s in trace["steps"])


async def test_rejected_action_is_never_executed(api, tokens, mock_api):
    result = await run(api, tokens["engineer"], "acme", TICKET)
    pending = result["pending_actions"][0]
    rejected = await api.post(
        f"/api/agent/{result['run_id']}/approve",
        headers=auth(tokens["engineer"], "acme"),
        json={
            "tool_call_id": pending["tool_call_id"],
            "decision": "reject",
            "comment": "duplicate of an existing ticket",
        },
    )
    body = rejected.json()
    assert body["status"] == "completed" and body["answer"]["needs_human_followup"]
    assert "not created" in " ".join(f["statement"] for f in body["answer"]["findings"])
    assert (await mock_api.get("/_admin/stats")).json()["tickets"] == []


async def test_concurrent_approvals_execute_the_write_exactly_once(api, tokens, mock_api):
    result = await run(api, tokens["engineer"], "acme", TICKET)
    decision = {"tool_call_id": result["pending_actions"][0]["tool_call_id"], "decision": "approve"}
    url = f"/api/agent/{result['run_id']}/approve"
    responses = await asyncio.gather(
        *[api.post(url, headers=auth(tokens["engineer"], "acme"), json=decision) for _ in range(4)]
    )
    assert sorted(r.status_code for r in responses) == [200, 409, 409, 409]
    assert len((await mock_api.get("/_admin/stats")).json()["tickets"]) == 1


async def test_disabled_tool_is_never_offered_to_the_agent(api, tokens):
    result = await run(api, tokens["engineer"], "betacorp", "Account ACC-2043 has late deliveries - open a ticket.")
    assert result["status"] == "completed" and result["pending_actions"] == []
    trace = (await api.get(f"/api/runs/{result['run_id']}", headers=auth(tokens["engineer"], "betacorp"))).json()
    discovery = next(s for s in trace["steps"] if s["step_type"] == "tool_discovery")["attributes"]
    assert "create_ticket" in discovery["disabled"] and "create_ticket" not in discovery["enabled"]
    assert [c["tool_name"] for c in trace["tool_calls"]] == ["get_customer"]  # served by the Postgres connector
    assert "Mesa Dairy" in result["answer"]["summary"]


async def test_run_listing_and_filters(api, tokens):
    runs = (await api.get("/api/runs?status=completed&limit=5", headers=auth(tokens["viewer"], "acme"))).json()
    assert runs and all(r["status"] == "completed" for r in runs)
