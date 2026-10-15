"""Run the tenant's agent, and approve/reject its write actions."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from redis.asyncio import Redis
from redis.exceptions import RedisError

from apps.api.deps import TenantContext, get_redis, get_runtime, tenant_scope
from packages.core.config import get_settings
from packages.core.metrics import RATE_LIMITED
from packages.core.ratelimit import check_rate_limit
from packages.schemas.api import AgentRunRequest, AgentRunResponse, ApprovalRequest, PendingAction, Usage
from services.agent.runtime import AgentRuntime, RunConflictError, RunNotFoundError, RunResult

router = APIRouter(prefix="/api/agent", tags=["agent"])


def to_response(result: RunResult) -> AgentRunResponse:
    return AgentRunResponse(
        run_id=result.run_id,
        tenant_id=result.tenant_id,
        status=result.status,
        answer=result.answer,
        pending_actions=[PendingAction(**p) for p in result.pending_actions],
        error=result.error,
        request_id=result.request_id,
        trace_id=result.trace_id,
        latency_ms=result.latency_ms,
        model=result.model,
        usage=Usage(**result.usage) if result.usage else Usage(),
    )


@router.post("/run", response_model=AgentRunResponse)
async def run_agent(
    body: AgentRunRequest,
    request: Request,
    response: Response,
    ctx: TenantContext = Depends(tenant_scope("agent:run", require_active=True)),
    runtime: AgentRuntime = Depends(get_runtime),
    redis: Redis = Depends(get_redis),
) -> AgentRunResponse:
    limit = get_settings().agent_rate_limit_per_minute
    try:
        verdict = await check_rate_limit(redis, f"agent:{ctx.tenant.id}", limit)
    except RedisError:
        verdict = None  # fail open: a Redis blip must not take the agent down (logged via metrics/alerts)
    if verdict is not None:
        response.headers["X-RateLimit-Limit"] = str(verdict.limit)
        response.headers["X-RateLimit-Remaining"] = str(verdict.remaining)
        if not verdict.allowed:
            RATE_LIMITED.labels(ctx.tenant.slug).inc()
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"agent rate limit of {limit}/min exceeded for this tenant",
                headers={"Retry-After": str(verdict.retry_after_s)},
            )
    try:
        result = await runtime.start(
            tenant=ctx.tenant,
            principal=ctx.principal,
            message=body.message.strip(),
            request_id=request.scope["state"].get("request_id"),
        )
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return to_response(result)


@router.post("/{run_id}/approve", response_model=AgentRunResponse)
async def approve_action(
    run_id: uuid.UUID,
    body: ApprovalRequest,
    ctx: TenantContext = Depends(tenant_scope("agent:approve")),
    runtime: AgentRuntime = Depends(get_runtime),
) -> AgentRunResponse:
    try:
        result = await runtime.decide(
            tenant=ctx.tenant,
            principal=ctx.principal,
            run_id=run_id,
            tool_call_id=body.tool_call_id,
            approve=body.decision == "approve",
            comment=body.comment,
        )
    except RunNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found") from exc
    except RunConflictError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return to_response(result)
