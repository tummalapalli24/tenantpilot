"""Mock of a customer's internal system (Acme Field Services "Field API v1").

It stands in for the system an FDE would integrate with on site. Its JSON shape is
intentionally *not* TenantPilot's canonical shape (customer_ref/full_name/work_orders
vs customer_id/name/jobs) so the connector's field mapping does real work.

Failure injection (for retry/timeout tests and demos):
    POST /_admin/failure {"mode": "http_500" | "http_503" | "timeout" | "none", "count": 3}
    GET  /_admin/stats   -> request counts per route
    POST /_admin/reset
"""

import asyncio
import itertools
import os
from collections import Counter
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from services.mock_customer_api.data import CUSTOMERS, OPEN_STATES, WORK_ORDERS

ACME_API_KEY = os.environ.get("ACME_API_KEY", "acme-demo-key-123")
ADMIN_TOKEN = os.environ.get("MOCK_ADMIN_TOKEN", "mock-admin-token")


class FailureState:
    def __init__(self) -> None:
        self.mode: str = "none"
        self.remaining: int | None = None
        self.stats: Counter[str] = Counter()
        self.tickets: list[dict] = []
        self.ticket_seq = itertools.count(5001)

    def reset(self) -> None:
        self.__init__()


state = FailureState()
app = FastAPI(title="Acme Field API (mock)", version="1.0")


@app.middleware("http")
async def inject_failures(request: Request, call_next):
    path = request.url.path
    if path.startswith("/acme/") and not path.endswith("/health"):
        state.stats[f"{request.method} {path}"] += 1
        state.stats["total"] += 1
        if state.mode != "none" and (state.remaining is None or state.remaining > 0):
            if state.remaining is not None:
                state.remaining -= 1
            state.stats["injected_failures"] += 1
            if state.mode == "timeout":
                await asyncio.sleep(30)
            status = 503 if state.mode == "http_503" else 500
            return JSONResponse({"error": "injected failure", "mode": state.mode}, status_code=status)
    return await call_next(request)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if x_api_key != ACME_API_KEY:
        raise HTTPException(status_code=401, detail="invalid API key")


acme = APIRouter(prefix="/acme/v1", dependencies=[Depends(require_api_key)])


@app.get("/acme/v1/health")
async def health() -> dict:
    return {"status": "ok", "system": "acme-field-api", "version": "1.0"}


@acme.get("/customers/{customer_ref}")
async def get_customer(customer_ref: str) -> dict:
    customer = CUSTOMERS.get(customer_ref.upper())
    if not customer:
        raise HTTPException(status_code=404, detail="customer not found")
    return {"customer": customer, "_links": {"self": f"/acme/v1/customers/{customer_ref}"}}


@acme.get("/customers/{customer_ref}/work-orders")
async def list_work_orders(customer_ref: str, state_filter: str = Query("open", alias="state")) -> dict:
    ref = customer_ref.upper()
    if ref not in CUSTOMERS:
        raise HTTPException(status_code=404, detail="customer not found")
    orders = [w for w in WORK_ORDERS if w["customer_ref"] == ref]
    if state_filter == "open":
        orders = [w for w in orders if w["state"] in OPEN_STATES]
    return {"data": {"work_orders": orders, "count": len(orders)}, "meta": {"filter": state_filter}}


class TicketIn(BaseModel):
    customer_ref: str = Field(max_length=20)
    subject: str = Field(min_length=3, max_length=500)
    severity: Literal["low", "medium", "high", "critical"]
    channel: str = "api"


@acme.post("/tickets", status_code=201)
async def create_ticket(body: TicketIn) -> dict:
    if body.customer_ref.upper() not in CUSTOMERS:
        raise HTTPException(status_code=404, detail="customer not found")
    ticket = {
        "ticket_no": f"TCK-{next(state.ticket_seq)}",
        "customer_ref": body.customer_ref.upper(),
        "subject": body.subject,
        "severity": body.severity,
        "state": "OPEN",
        "channel": body.channel,
        "opened_at": datetime.now(UTC).isoformat(),
    }
    state.tickets.append(ticket)
    return {"ticket": ticket}


app.include_router(acme)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if x_admin_token != ADMIN_TOKEN:
        raise HTTPException(status_code=403, detail="admin token required")


class FailureIn(BaseModel):
    mode: Literal["none", "http_500", "http_503", "timeout"]
    count: int | None = Field(default=None, ge=1, description="Fail the next N requests; null = until reset")


@app.post("/_admin/failure", dependencies=[Depends(require_admin)])
async def set_failure(body: FailureIn) -> dict:
    state.mode, state.remaining = body.mode, body.count
    return {"mode": state.mode, "remaining": state.remaining}


@app.get("/_admin/stats", dependencies=[Depends(require_admin)])
async def stats() -> dict:
    return {"stats": dict(state.stats), "tickets": state.tickets, "failure_mode": state.mode}


@app.post("/_admin/reset", dependencies=[Depends(require_admin)])
async def reset() -> dict:
    state.reset()
    return {"ok": True}


@app.get("/health")
async def root_health() -> dict:
    return {"status": "ok"}
