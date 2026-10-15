"""Liveness, readiness and model availability.

Liveness (/api/health) checks only that the process can serve HTTP - if it depended on
the database, a DB outage would make Kubernetes restart every healthy API pod.
Readiness (/api/ready) checks the dependencies needed to serve traffic (Postgres,
Redis); a failing check takes the pod out of the Service until it recovers. The MCP
server is not required: without it the agent degrades to knowledge-only answers.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from apps.api.deps import require
from packages.core.config import get_settings
from packages.core.db import platform_session
from packages.core.security import Principal
from packages.schemas.api import ModelInfo
from services.agent.llm.models import OFFLINE_MODEL, SELECTABLE_MODELS, capabilities

router = APIRouter(tags=["health"])


@router.get("/api/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/api/ready")
async def ready(request: Request) -> JSONResponse:
    checks: dict[str, str] = {}
    try:
        async with platform_session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    try:
        await request.app.state.redis.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {type(exc).__name__}"
    # The MCP server is deliberately NOT probed here: a slow dependency would time out the
    # probe and pull healthy API pods out of rotation. Tool discovery degrades per request.
    ok = checks["database"] == "ok" and checks["redis"] == "ok"
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)


@router.get("/api/models", response_model=list[ModelInfo])
async def models(_: Principal = Depends(require("tenants:read"))) -> list[ModelInfo]:
    llm = get_settings().llm_available
    out = []
    for model in SELECTABLE_MODELS:
        if model == OFFLINE_MODEL:
            out.append(
                ModelInfo(
                    id=model,
                    provider="offline",
                    available=True,
                    supports_temperature=False,
                    supports_effort=False,
                    note="Deterministic planner for CI/demos; no API key needed",
                )
            )
            continue
        caps = capabilities(model)
        out.append(
            ModelInfo(
                id=model,
                provider="anthropic",
                available=llm,
                supports_temperature=caps.sampling,
                supports_effort=caps.effort,
                note=None if llm else "Set ANTHROPIC_API_KEY to enable Claude models",
            )
        )
    return out
