"""TenantPilot control-plane API (FastAPI). OpenAPI docs at /api/docs."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app
from redis.asyncio import Redis

from apps.api.middleware import RequestContextMiddleware
from apps.api.routers import agent, auth, connectors, documents, health, runs, tenants, tools
from packages.core.config import get_settings
from packages.core.db import dispose_engine, get_engine
from packages.core.logging import configure_logging, request_id_var
from packages.core.telemetry import setup_telemetry
from services.agent.mcp_gateway import MCPGateway
from services.agent.runtime import AgentRuntime

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.gateway = MCPGateway(settings.mcp_server_url)
    app.state.runtime = AgentRuntime(app.state.gateway)
    app.state.redis = Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2)
    get_engine()
    log.info(
        "api started",
        extra={
            "environment": settings.environment,
            "mcp_server_url": settings.mcp_server_url,
            "llm_available": settings.llm_available,
        },
    )
    yield
    await app.state.redis.aclose()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("tenantpilot-api", settings.log_level, settings.log_format)
    setup_telemetry("tenantpilot-api")
    app = FastAPI(
        title="TenantPilot API",
        version="0.1.0",
        description="Control plane for multi-tenant AI agent deployments.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestContextMiddleware)
    for router in (health, auth, tenants, connectors, documents, tools, agent, runs):
        app.include_router(router.router)
    app.mount("/metrics", make_asgi_app())

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            {"detail": exc.detail, "request_id": request_id_var.get()}, status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": e.get("loc"), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": errors, "request_id": request_id_var.get()}, status_code=422)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return JSONResponse({"detail": "internal server error", "request_id": request_id_var.get()}, status_code=500)

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, excluded_urls="api/health,api/ready,metrics")
    except Exception:  # pragma: no cover
        log.warning("FastAPI instrumentation unavailable")
    return app


app = create_app()
