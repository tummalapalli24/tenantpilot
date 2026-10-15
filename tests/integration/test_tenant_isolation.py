"""Security: one tenant must never read another tenant's data (expected: 403 or zero results)."""

import uuid

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from sqlalchemy import func, select, text

from packages.core.db import platform_session, tenant_session
from packages.core.models import DocumentChunk, Tenant
from packages.core.security import create_service_token
from packages.knowledge.retriever import search_chunks
from tests.conftest import auth

pytestmark = pytest.mark.integration


async def _tenant_id(slug: str) -> uuid.UUID:
    async with platform_session() as session:
        return (await session.execute(select(Tenant.id).where(Tenant.slug == slug))).scalar_one()


async def test_member_of_one_tenant_gets_403_on_another(api, tokens):
    token = tokens["acme-engineer"]  # member of acme only
    assert (await api.get("/api/tenants/betacorp", headers=auth(token))).status_code == 403
    assert (await api.get("/api/runs", headers=auth(token, "betacorp"))).status_code == 403
    assert (await api.get(f"/api/tenants/{uuid.uuid4()}", headers=auth(token))).status_code == 403  # no probing
    listed = (await api.get("/api/tenants", headers=auth(token))).json()
    assert [t["slug"] for t in listed] == ["acme"]


async def test_another_tenants_run_is_invisible_through_rls(api, tokens):
    beta_run = await api.post(
        "/api/agent/run",
        headers=auth(tokens["engineer"], "betacorp"),
        json={"message": "What is the excursion reporting SLA?"},
    )
    run_id = beta_run.json()["run_id"]
    as_acme = await api.get(f"/api/runs/{run_id}", headers=auth(tokens["engineer"], "acme"))
    assert as_acme.status_code == 404  # scoped to acme, the betacorp row simply doesn't exist
    assert (await api.get(f"/api/runs/{run_id}", headers=auth(tokens["engineer"], "betacorp"))).status_code == 200


async def test_rls_returns_zero_rows_without_tenant_context_and_filters_with_it():
    acme = await _tenant_id("acme")
    async with platform_session() as session:  # no app.current_tenant set
        assert await session.scalar(select(func.count()).select_from(DocumentChunk)) == 0
    async with tenant_session(acme) as session:
        # deliberately no WHERE tenant_id: RLS must still scope the rows
        tenants = (await session.execute(select(DocumentChunk.tenant_id).distinct())).scalars().all()
        assert tenants == [acme]


async def test_vector_search_never_crosses_tenants():
    acme, beta = await _tenant_id("acme"), await _tenant_id("betacorp")
    async with tenant_session(acme) as session:
        hits = await search_chunks(session, acme, "reefer temperature excursion deviation report", top_k=20)
    assert hits and all(not h.document.startswith(("cold-chain", "account-handbook")) for h in hits)
    async with tenant_session(acme) as session:  # asking for betacorp's rows while scoped to acme
        assert await search_chunks(session, beta, "excursion", top_k=5) == []


async def test_cannot_write_rows_for_another_tenant():
    acme, beta = await _tenant_id("acme"), await _tenant_id("betacorp")
    with pytest.raises(Exception, match="row-level security"):
        async with tenant_session(acme) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_logs (id, tenant_id, action, resource_type, details, created_at) "
                    "VALUES (gen_random_uuid(), :t, 'x', 'x', '{}', now())"
                ),
                {"t": str(beta)},
            )


async def test_mcp_server_enforces_tenant_and_tool_scope_itself():
    """Defense in depth: even a caller holding a valid token can only use the tools and the
    tenant pinned in that token."""
    from packages.core.config import get_settings

    url = get_settings().mcp_server_url
    beta = await _tenant_id("betacorp")
    token = create_service_token(tenant_id=str(beta), run_id=None, user_id=None, allowed_tools=["get_customer"])
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            denied = await client.call_tool(
                "create_ticket", {"customer_id": "C-104", "summary": "should not work", "priority": "low"}
            )
            assert denied.is_error and "not enabled for this tenant" in denied.content[0].text
            # C-104 is an Acme customer; with a BetaCorp token the lookup hits BetaCorp's CRM only
            other = await client.call_tool("get_customer", {"customer_id": "C-104"})
            assert other.is_error and "No customer" in other.content[0].text
            own = await client.call_tool("get_customer", {"customer_id": "ACC-2041"})
            assert not own.is_error and own.structured_content["name"] == "Sonoran Fresh Produce"


async def test_mcp_server_rejects_missing_or_forged_tokens():
    from packages.core.config import get_settings

    url = get_settings().mcp_server_url
    async with httpx2.AsyncClient() as http:
        resp = await http.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code == 401
        forged = await http.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "Accept": "application/json, text/event-stream",
                "Authorization": "Bearer eyJhbGciOiJub25lIn0.e30.",
            },
        )
        assert forged.status_code == 401
