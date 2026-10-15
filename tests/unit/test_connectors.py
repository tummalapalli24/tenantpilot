"""Connector mapping, templating, retries and failure classification (no network)."""

import httpx
import pytest

from packages.connectors.base import ConnectorError
from packages.connectors.mapping import get_path, map_fields, render_path, render_template
from packages.connectors.postgres import build_dsn
from packages.connectors.rest import RestApiConnector
from packages.connectors.templates import FIELD_SERVICE_REST_V1
from packages.schemas.connectors import PostgresOperation, RestConnectorConfig

ACME_CUSTOMER = {
    "customer": {
        "customer_ref": "C-104",
        "full_name": "Harbor View Dental",
        "account_status": "ACTIVE",
        "plan": "Pro",
        "site": {"region": "Tucson"},
        "contact": {"name": "Maria"},
        "open_tickets": 2,
        "account_notes": "n",
    }
}


def test_get_path_and_field_mapping():
    assert get_path({"a": {"b": [{"c": 7}]}}, "a.b.0.c") == 7
    assert get_path({"a": 1}, "a.missing.deeper") is None
    mapped = map_fields(ACME_CUSTOMER["customer"], {"customer_id": "customer_ref", "region": "site.region"})
    assert mapped == {"customer_id": "C-104", "region": "Tucson"}


def test_render_path_url_encodes_values():
    assert render_path("/customers/{customer_id}", {"customer_id": "../admin?x=1"}) == "/customers/..%2Fadmin%3Fx%3D1"
    with pytest.raises(ConnectorError):
        render_path("/customers/{customer_id}", {})


def test_render_template_keeps_types_for_exact_placeholders():
    body = render_template(
        {"ref": "{customer_id}", "n": "{count}", "text": "id={customer_id}"}, {"customer_id": "C-1", "count": 3}
    )
    assert body == {"ref": "C-1", "n": 3, "text": "id=C-1"}


def make_connector(handler) -> tuple[RestApiConnector, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request, len(seen))

    config = RestConnectorConfig.model_validate(FIELD_SERVICE_REST_V1)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    return RestApiConnector("acme", "http://acme.test/v1", "secret-key", config, client=client), seen


async def test_rest_connector_maps_response_and_sends_auth_header():
    connector, seen = make_connector(lambda req, n: httpx.Response(200, json=ACME_CUSTOMER))
    record = await connector.execute("get_customer", {"customer_id": "C-104"})
    assert record["customer_id"] == "C-104" and record["name"] == "Harbor View Dental"
    assert record["region"] == "Tucson" and record["open_ticket_count"] == 2
    assert seen[0].headers["X-API-Key"] == "secret-key"
    assert seen[0].url.path == "/v1/customers/C-104"


async def test_rest_connector_retries_transient_errors_then_succeeds():
    connector, seen = make_connector(
        lambda req, n: httpx.Response(503) if n < 3 else httpx.Response(200, json=ACME_CUSTOMER)
    )
    record = await connector.execute("get_customer", {"customer_id": "C-104"})
    assert record["customer_id"] == "C-104" and len(seen) == 3


async def test_rest_connector_stops_after_max_attempts_with_controlled_error():
    connector, seen = make_connector(lambda req, n: httpx.Response(500))
    with pytest.raises(ConnectorError) as err:
        await connector.execute("get_customer", {"customer_id": "C-104"})
    assert err.value.kind == "unavailable" and "after 3 attempts" in str(err.value)
    assert len(seen) == 3


@pytest.mark.parametrize("status,kind", [(404, "not_found"), (401, "unauthorized"), (400, "bad_request")])
async def test_rest_connector_does_not_retry_client_errors(status, kind):
    connector, seen = make_connector(lambda req, n: httpx.Response(status))
    with pytest.raises(ConnectorError) as err:
        await connector.execute("get_customer", {"customer_id": "C-104"})
    assert err.value.kind == kind and len(seen) == 1


async def test_rest_connector_list_and_write_operations():
    orders = {
        "data": {
            "work_orders": [
                {
                    "wo_number": "J-774",
                    "customer_ref": "C-104",
                    "state": "FAILED",
                    "fault_code": "E-ACT-401",
                    "tech": {"name": "D. Chen"},
                }
            ]
        }
    }

    def handler(req: httpx.Request, n: int) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(
                201,
                json={
                    "ticket": {
                        "ticket_no": "TCK-1",
                        "customer_ref": "C-104",
                        "subject": req.read().decode(),
                        "severity": "high",
                        "state": "OPEN",
                    }
                },
            )
        assert req.url.params["state"] == "open"
        return httpx.Response(200, json=orders)

    connector, seen = make_connector(handler)
    jobs = await connector.execute("list_open_jobs", {"customer_id": "C-104"})
    assert jobs[0]["job_id"] == "J-774" and jobs[0]["technician"] == "D. Chen"
    ticket = await connector.execute("create_ticket", {"customer_id": "C-104", "summary": "Broken", "priority": "high"})
    assert ticket["ticket_id"] == "TCK-1"
    assert '"severity":"high"' in ticket["summary"].replace(" ", "")


def test_postgres_operations_must_be_read_only_single_statements():
    PostgresOperation(sql="SELECT * FROM t WHERE id = $1", params=["id"])
    for bad in ("DELETE FROM t", "SELECT 1; DROP TABLE t", "update t set x=1"):
        with pytest.raises(ValueError):
            PostgresOperation(sql=bad)


def test_build_dsn_inserts_quoted_password():
    dsn = build_dsn("postgresql://reader@db:5432/crm", "p@ss/word")
    assert dsn == "postgresql://reader:p%40ss%2Fword@db:5432/crm"
