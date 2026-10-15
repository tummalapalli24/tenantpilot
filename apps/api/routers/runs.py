"""Agent runs and their traces (tenant from the X-Tenant-ID header)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from apps.api.deps import TenantContext, tenant_scope
from packages.core.db import tenant_session
from packages.core.models import AgentRun, RunStep, ToolCall
from packages.schemas.api import RunDetail, RunStepOut, RunSummary, ToolCallOut

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=list[RunSummary])
async def list_runs(
    ctx: TenantContext = Depends(tenant_scope("runs:read")),
    status_filter: str | None = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[RunSummary]:
    async with tenant_session(ctx.tenant.id) as session:
        query = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit).offset(offset)
        if status_filter:
            query = query.where(AgentRun.status == status_filter)
        return [RunSummary.model_validate(r) for r in (await session.execute(query)).scalars()]


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, ctx: TenantContext = Depends(tenant_scope("runs:read"))) -> RunDetail:
    async with tenant_session(ctx.tenant.id) as session:
        # RLS: a run belonging to another tenant is simply invisible here -> 404.
        run = await session.get(AgentRun, run_id)
        if run is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
        steps = (
            (await session.execute(select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.seq)))
            .scalars()
            .all()
        )
        calls = (
            (await session.execute(select(ToolCall).where(ToolCall.run_id == run_id).order_by(ToolCall.created_at)))
            .scalars()
            .all()
        )
        return RunDetail(
            **RunSummary.model_validate(run).model_dump(),
            response=run.response,
            error=run.error,
            request_id=run.request_id,
            trace_id=run.trace_id,
            cache_read_tokens=run.cache_read_tokens,
            llm_calls=run.llm_calls,
            completed_at=run.completed_at,
            steps=[RunStepOut.model_validate(s) for s in steps],
            tool_calls=[ToolCallOut.model_validate(c) for c in calls],
        )
