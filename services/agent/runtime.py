"""The agent runtime: a manual tool-use loop with governance, approvals, and tracing.

    1. receive task                       -> agent_runs row (status=running)
    2. inspect tenant configuration       -> model, prompt, budget, tool permissions
    3. discover tools (MCP tools/list)    -> filtered to the tenant's enabled tools
    4. retrieve knowledge (pgvector)      -> candidates for the ContextBuilder
    5. build context (budgeted)           -> system prompt + structured user message
    6. LLM decides: answer or call tools
    7. each tool call: policy -> argument validation -> execute via MCP | pause for approval
    8. rebuild context with tool results  -> loop to 6
    9. write actions wait for a human     -> status=awaiting_approval, state persisted
   10. validated structured answer        -> status=completed; every step saved as a trace

Why a manual loop rather than an SDK tool runner: a run must be able to *stop* at an
approval gate and resume minutes later in a different HTTP request - possibly on a
different API pod - from state stored in Postgres. So the transcript is persisted after
every turn and the loop is re-entered from that state.
"""

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import select

from packages.context.builder import ContextBuilder
from packages.core.audit import record_audit
from packages.core.config import get_settings
from packages.core.db import tenant_session
from packages.core.logging import redact, run_id_var
from packages.core.metrics import (
    AGENT_ERRORS,
    AGENT_LATENCY,
    AGENT_REQUESTS,
    APPROVALS,
    LLM_TOKENS,
    TOOL_CALLS,
    TOOL_FAILURES,
)
from packages.core.models import AgentRun, TenantConfig, ToolCall, ToolPermission, utcnow
from packages.core.security import Principal, create_service_token
from packages.core.telemetry import current_trace_id, span
from packages.knowledge.retriever import search_chunks
from packages.schemas.agent import FINAL_ANSWER_JSON_SCHEMA, AgentAction, FinalAnswer, parse_final_answer
from packages.schemas.api import CatalogTool
from services.agent.llm.base import LLMError, ModelConfig, ToolSpec
from services.agent.llm.models import capabilities, get_provider
from services.agent.mcp_gateway import MCPGateway, MCPUnavailableError, ToolOutcome
from services.agent.policy import ToolPolicy, authorize, resolve_policies, validate_arguments
from services.agent.recorder import RunRecorder

log = logging.getLogger(__name__)


class RunNotFoundError(Exception):
    pass


class RunConflictError(Exception):
    pass


@dataclass
class TenantRef:
    id: uuid.UUID
    slug: str
    name: str


@dataclass
class RunResult:
    run_id: uuid.UUID
    tenant_id: uuid.UUID
    status: str
    model: str
    answer: dict[str, Any] | None = None
    pending_actions: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    request_id: str | None = None
    trace_id: str | None = None
    latency_ms: int | None = None
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class _Planned:
    """One tool_use block from the model, after policy + validation."""

    tool_use: dict[str, Any]
    source_id: str
    decision: str  # allow | deny | invalid | approval
    reason: str


class AgentRuntime:
    def __init__(self, gateway: MCPGateway):
        self.gateway = gateway
        self.settings = get_settings()

    # ================================================================== public API
    async def start(
        self, *, tenant: TenantRef, principal: Principal, message: str, request_id: str | None
    ) -> RunResult:
        t0 = time.perf_counter()
        async with tenant_session(tenant.id) as session:
            cfg = await session.get(TenantConfig, tenant.id)
            if cfg is None:
                raise RunConflictError("tenant has no agent configuration")
            perms = {p.tool_name: p for p in (await session.execute(select(ToolPermission))).scalars()}
            run = AgentRun(
                tenant_id=tenant.id,
                user_id=principal.user_id,
                request=message,
                status="running",
                model=cfg.model_name,
                request_id=request_id,
            )
            session.add(run)
            await session.flush()
            run_id = run.id
        run_id_var.set(str(run_id))
        recorder = RunRecorder(tenant.id, run_id)
        with span("agent.run", **{"tenant.id": str(tenant.id), "tenant.slug": tenant.slug, "run.id": str(run_id)}):
            trace_id = current_trace_id()
            state: dict[str, Any] = {
                "trace_id": trace_id,
                "request_id": request_id,
                "latency_ms": 0,
                "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "llm_calls": 0},
            }
            try:
                state.update(await self._prepare(tenant, cfg, perms, message, recorder))
                return await self._loop(tenant, run_id, principal, state, recorder, t0)
            except LLMError as exc:
                return await self._finish(tenant, run_id, state, t0, "failed", error=str(exc), error_kind=exc.kind)
            except Exception as exc:
                log.exception("agent run crashed")
                return await self._finish(
                    tenant,
                    run_id,
                    state,
                    t0,
                    "failed",
                    error=f"internal error ({type(exc).__name__}); see trace {trace_id}",
                    error_kind="internal",
                )

    async def decide(
        self,
        *,
        tenant: TenantRef,
        principal: Principal,
        run_id: uuid.UUID,
        tool_call_id: uuid.UUID,
        approve: bool,
        comment: str | None,
    ) -> RunResult:
        """Apply a human decision to a pending write action and resume the run.

        The run row is locked (SELECT ... FOR UPDATE) and moved out of
        ``awaiting_approval`` inside the same transaction, so two operators clicking
        Approve at the same moment can never execute the write twice: the second one
        gets RunConflictError (HTTP 409)."""
        t0 = time.perf_counter()
        decision = "approve" if approve else "reject"
        async with tenant_session(tenant.id) as session:
            run = (
                await session.execute(select(AgentRun).where(AgentRun.id == run_id).with_for_update())
            ).scalar_one_or_none()
            if run is None:
                raise RunNotFoundError()
            if run.status != "awaiting_approval":
                raise RunConflictError(f"run is '{run.status}', not awaiting approval")
            call = (
                await session.execute(
                    select(ToolCall).where(ToolCall.id == tool_call_id, ToolCall.run_id == run_id).with_for_update()
                )
            ).scalar_one_or_none()
            if call is None or call.status != "pending_approval":
                raise RunConflictError("that tool call is not pending approval")
            call.approval_status = "approved" if approve else "rejected"
            call.approved_by, call.approved_at = principal.user_id, utcnow()
            call.status = "approved" if approve else "rejected"
            run.status = "running"  # claim the run while still holding the lock
            state: dict[str, Any] = dict(run.state or {})
            tool_use_id, tool_name, arguments = call.tool_use_id, call.tool_name, dict(call.request_json)
            await record_audit(
                session,
                tenant_id=tenant.id,
                actor_id=principal.user_id,
                actor_email=principal.email,
                action=f"agent.action.{decision}",
                resource_type="tool_call",
                resource_id=str(tool_call_id),
                details={"tool": tool_name, "run_id": str(run_id), "arguments": arguments, "comment": comment},
            )
        APPROVALS.labels(tenant.slug, decision).inc()
        run_id_var.set(str(run_id))
        recorder = await RunRecorder.resume(tenant.id, run_id)
        with span("agent.resume", **{"tenant.id": str(tenant.id), "run.id": str(run_id), "decision": decision}):
            try:
                pending = state["pending"]
                async with recorder.step("approval", f"{decision} {tool_name}") as st:
                    st.update(
                        decision=decision,
                        actor=principal.email,
                        comment=comment,
                        tool=tool_name,
                        arguments=arguments,
                        resumed_trace_id=current_trace_id(),
                    )
                source_id = pending["source_ids"][tool_use_id]
                if approve:
                    policies = await self._current_policies(tenant.id, state["tools"])
                    verdict = authorize(policies, tool_name)
                    if verdict.outcome == "deny":
                        block = await self._record_outcome(
                            tenant,
                            run_id,
                            state,
                            recorder,
                            tool_use_id,
                            tool_name,
                            arguments,
                            source_id,
                            ToolOutcome(False, error=f"{verdict.reason} (changed before approval executed)"),
                            status="denied",
                            existing_call_id=tool_call_id,
                        )
                    else:
                        outcomes = await self._execute(
                            tenant, run_id, principal, [(tool_use_id, tool_name, arguments)], allowed=[tool_name]
                        )
                        block = await self._record_outcome(
                            tenant,
                            run_id,
                            state,
                            recorder,
                            tool_use_id,
                            tool_name,
                            arguments,
                            source_id,
                            outcomes[tool_use_id],
                            existing_call_id=tool_call_id,
                        )
                else:
                    reason = "A human operator rejected this action" + (f": {comment}" if comment else ".")
                    block, _, _ = self._tool_result_block(
                        state,
                        tool_use_id,
                        tool_name,
                        source_id,
                        ToolOutcome(False, error=f"{reason} Do not retry it; explain the situation instead."),
                    )
                    async with tenant_session(tenant.id) as session:
                        row = await session.get(ToolCall, tool_call_id)
                        row.response_json = {"error": reason}
                    TOOL_CALLS.labels(tenant.slug, tool_name, "rejected").inc()
                pending["results"][tool_use_id] = block
                pending["awaiting"].pop(tool_use_id, None)
                if pending["awaiting"]:
                    return await self._pause(tenant, run_id, state, t0)
                state["messages"].append({"role": "user", "content": [pending["results"][i] for i in pending["order"]]})
                state["pending"] = None
                return await self._loop(tenant, run_id, principal, state, recorder, t0)
            except LLMError as exc:
                return await self._finish(tenant, run_id, state, t0, "failed", error=str(exc), error_kind=exc.kind)
            except Exception as exc:
                log.exception("agent resume crashed")
                return await self._finish(
                    tenant,
                    run_id,
                    state,
                    t0,
                    "failed",
                    error=f"internal error ({type(exc).__name__})",
                    error_kind="internal",
                )

    # ================================================================== preparation
    async def _prepare(
        self,
        tenant: TenantRef,
        cfg: TenantConfig,
        perms: dict[str, ToolPermission],
        message: str,
        recorder: RunRecorder,
    ) -> dict[str, Any]:
        async with recorder.step("tool_discovery", "MCP tools/list") as st:
            catalog = []
            try:
                catalog = await self.gateway.catalog()
            except MCPUnavailableError as exc:
                # Degrade instead of failing: the agent can still answer from knowledge.
                st.update(status="error", error=str(exc), degraded=True)
            policies = resolve_policies(catalog, perms, cfg.approval_required)
            enabled = [t for t in catalog if policies[t.name].enabled]
            st.update(
                discovered=[t.name for t in catalog],
                enabled=[t.name for t in enabled],
                approval_gated=[t.name for t in enabled if policies[t.name].requires_approval],
                disabled=[t.name for t in catalog if not policies[t.name].enabled],
            )

        async with recorder.step("retrieval", "pgvector cosine search") as st:
            async with tenant_session(tenant.id) as session:
                candidates = await search_chunks(
                    session, tenant.id, message, top_k=cfg.retrieval_top_k * 2, tenant_label=tenant.slug
                )
            st.update(
                candidates=len(candidates),
                requested=cfg.retrieval_top_k * 2,
                top_scores=[c.vector_score for c in candidates[:5]],
            )

        async with recorder.step("context_build", "ContextBuilder") as st:
            builder = ContextBuilder(max_context_tokens=cfg.max_context_tokens)
            bundle = builder.build(
                request=message,
                tenant_name=tenant.name,
                tenant_instructions=cfg.system_prompt,
                candidates=candidates[: max(cfg.retrieval_top_k * 2, 1)],
                json_only=not capabilities(cfg.model_name).structured_output,
            )
            st.update(bundle.trace_attributes())

        return {
            "system": bundle.system_prompt,
            "messages": [{"role": "user", "content": bundle.user_message}],
            "tools": [asdict(ToolSpec(t.name, t.description, t.input_schema, t.read_only)) for t in enabled],
            "model": asdict(
                ModelConfig(model=cfg.model_name, temperature=cfg.temperature, effort=cfg.reasoning_effort)
            ),
            "source_ids": sorted(bundle.source_ids),
            "tool_seq": 0,
            "tool_result_max_tokens": bundle.tool_result_max_tokens,
            "iterations": 0,
            "pending": None,
        }

    # ================================================================== the loop
    async def _loop(
        self,
        tenant: TenantRef,
        run_id: uuid.UUID,
        principal: Principal,
        state: dict[str, Any],
        recorder: RunRecorder,
        t0: float,
    ) -> RunResult:
        model_cfg = ModelConfig(**state["model"])
        provider = get_provider(model_cfg.model)
        tools = [ToolSpec(**t) for t in state["tools"]]
        while state["iterations"] < self.settings.agent_max_iterations:
            state["iterations"] += 1
            async with recorder.step("llm_call", f"{provider.name}:{model_cfg.model}") as st:
                response = await provider.complete(
                    system=state["system"],
                    messages=state["messages"],
                    tools=tools,
                    config=model_cfg,
                    output_schema=FINAL_ANSWER_JSON_SCHEMA,
                )
                st.update(
                    model=response.model,
                    stop_reason=response.stop_reason,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                    cache_read_tokens=response.usage.cache_read_tokens,
                    notes=response.notes,
                    anthropic_request_id=response.request_id,
                    text_preview=response.text[:600],
                    thinking_summary=response.thinking_summary[:2000] or None,
                    actions=[
                        AgentAction(
                            action="tool_call", tool=b["name"], arguments=b.get("input") or {}, tool_use_id=b["id"]
                        ).model_dump()
                        for b in response.tool_uses
                    ]
                    or [AgentAction(action="final_answer").model_dump()],
                )
            usage = state["usage"]
            usage["input_tokens"] += response.usage.input_tokens
            usage["output_tokens"] += response.usage.output_tokens
            usage["cache_read_tokens"] += response.usage.cache_read_tokens
            usage["llm_calls"] += 1
            LLM_TOKENS.labels(tenant.slug, model_cfg.model, "input").inc(response.usage.input_tokens)
            LLM_TOKENS.labels(tenant.slug, model_cfg.model, "output").inc(response.usage.output_tokens)
            state["messages"].append({"role": "assistant", "content": response.content})

            if response.stop_reason == "refusal":
                return await self._finish(
                    tenant,
                    run_id,
                    state,
                    t0,
                    "failed",
                    error_kind="refused",
                    error=f"The model declined this request ({response.refusal_category or 'policy'}).",
                )
            if response.stop_reason == "max_tokens":
                return await self._finish(
                    tenant,
                    run_id,
                    state,
                    t0,
                    "failed",
                    error_kind="truncated",
                    error="The model's response hit max_tokens before finishing.",
                )
            if not response.tool_uses:
                answer, validation = parse_final_answer(response.text, set(state["source_ids"]))
                async with recorder.step("final_answer", "validate structured answer") as st:
                    st.update(validation.model_dump(), confidence=answer.confidence, findings=len(answer.findings))
                    if not validation.schema_valid or validation.unsupported_source_ids:
                        st["status"] = "warning"
                return await self._finish(tenant, run_id, state, t0, "completed", answer=answer)

            # ---- tool calls requested by the model
            policies = await self._current_policies(tenant.id, state["tools"])
            specs = {t.name: t for t in tools}
            planned: list[_Planned] = []
            for block in response.tool_uses:
                state["tool_seq"] += 1
                source_id = f"T{state['tool_seq']}"
                verdict = authorize(policies, block["name"])
                if verdict.outcome == "deny":
                    planned.append(_Planned(block, source_id, "deny", verdict.reason))
                    continue
                problem = validate_arguments(specs[block["name"]].input_schema, block.get("input"))
                if problem:
                    planned.append(_Planned(block, source_id, "invalid", problem))
                else:
                    planned.append(_Planned(block, source_id, verdict.outcome, verdict.reason))

            to_run = [
                (p.tool_use["id"], p.tool_use["name"], p.tool_use.get("input") or {})
                for p in planned
                if p.decision == "allow"
            ]
            outcomes = (
                await self._execute(
                    tenant, run_id, principal, to_run, allowed=[n for n, p in policies.items() if p.enabled]
                )
                if to_run
                else {}
            )
            results: dict[str, dict[str, Any]] = {}
            awaiting: dict[str, str] = {}
            source_ids: dict[str, str] = {}
            for p in planned:
                tu_id, name, args = p.tool_use["id"], p.tool_use["name"], p.tool_use.get("input") or {}
                source_ids[tu_id] = p.source_id
                if p.decision == "approval":
                    awaiting[tu_id] = str(
                        await self._record_pending(tenant, run_id, recorder, tu_id, name, args, p.source_id, p.reason)
                    )
                    continue
                if p.decision == "allow":
                    outcome, status = outcomes[tu_id], None
                else:
                    outcome = ToolOutcome(False, error=p.reason)
                    status = "denied" if p.decision == "deny" else "invalid_arguments"
                results[tu_id] = await self._record_outcome(
                    tenant, run_id, state, recorder, tu_id, name, args, p.source_id, outcome, status=status
                )
            if awaiting:
                state["pending"] = {
                    "order": [p.tool_use["id"] for p in planned],
                    "results": results,
                    "awaiting": awaiting,
                    "source_ids": source_ids,
                }
                return await self._pause(tenant, run_id, state, t0)
            state["messages"].append({"role": "user", "content": [results[p.tool_use["id"]] for p in planned]})

        return await self._finish(
            tenant,
            run_id,
            state,
            t0,
            "failed",
            error_kind="iteration_limit",
            error=f"The agent did not finish within {self.settings.agent_max_iterations} steps.",
        )

    # ================================================================== tools
    async def _current_policies(self, tenant_id: uuid.UUID, tool_snapshot: list[dict]) -> dict[str, ToolPolicy]:
        """Re-read permissions on every turn so an admin revoking a tool takes effect
        immediately, even for runs already in flight."""
        async with tenant_session(tenant_id) as session:
            cfg = await session.get(TenantConfig, tenant_id)
            perms = {p.tool_name: p for p in (await session.execute(select(ToolPermission))).scalars()}
        catalog = [
            CatalogTool(
                name=t["name"],
                description=t["description"],
                input_schema=t["input_schema"],
                read_only=t["read_only"],
                risk="read" if t["read_only"] else "write",
            )
            for t in tool_snapshot
        ]
        return resolve_policies(catalog, perms, cfg.approval_required if cfg else True)

    async def _execute(
        self,
        tenant: TenantRef,
        run_id: uuid.UUID,
        principal: Principal,
        calls: list[tuple[str, str, dict[str, Any]]],
        allowed: list[str],
    ) -> dict[str, ToolOutcome]:
        token = create_service_token(
            tenant_id=str(tenant.id), run_id=str(run_id), user_id=str(principal.user_id), allowed_tools=allowed
        )
        outcomes: dict[str, ToolOutcome] = {}
        try:
            async with self.gateway.session(token) as client:
                for tool_use_id, name, arguments in calls:
                    with span("mcp.call_tool", **{"gen_ai.tool.name": name}):
                        outcomes[tool_use_id] = await MCPGateway.call(client, name, arguments)
        except Exception as exc:
            log.warning("MCP session failed", extra={"error": type(exc).__name__})
        for tool_use_id, _name, _args in calls:
            outcomes.setdefault(tool_use_id, ToolOutcome(False, error="tool server unreachable; try again later"))
        return outcomes

    @staticmethod
    def _tool_result_block(
        state: dict[str, Any], tool_use_id: str, name: str, source_id: str, outcome: ToolOutcome
    ) -> tuple[dict[str, Any], int, bool]:
        """Build the tool_result block the model sees. Results are labeled with a source id
        (T1, T2...) so the final answer can cite them, then fitted to the tool budget."""
        payload: dict[str, Any] = {"source_id": source_id, "tool": name, "ok": outcome.ok}
        if outcome.ok:
            payload["result"] = redact(outcome.data)  # never feed credentials into model context
        else:
            payload["error"] = outcome.error
        fitted, truncated, tokens = ContextBuilder.fit_tool_result(
            json.dumps(payload, default=str, ensure_ascii=False), state["tool_result_max_tokens"]
        )
        if source_id not in state["source_ids"]:
            state["source_ids"].append(source_id)
        block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": fitted}
        if not outcome.ok:
            block["is_error"] = True
        return block, tokens, truncated

    async def _record_outcome(
        self,
        tenant: TenantRef,
        run_id: uuid.UUID,
        state: dict[str, Any],
        recorder: RunRecorder,
        tool_use_id: str,
        name: str,
        arguments: dict[str, Any],
        source_id: str,
        outcome: ToolOutcome,
        status: str | None = None,
        existing_call_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        status = status or ("succeeded" if outcome.ok else "failed")
        block, tokens, truncated = self._tool_result_block(state, tool_use_id, name, source_id, outcome)
        async with recorder.step("tool_call", f"{name}") as st:
            st.update(
                tool=name,
                source_id=source_id,
                arguments=arguments,
                outcome=status,
                duration_ms=outcome.latency_ms,
                result_tokens=tokens,
                truncated=truncated,
                error=outcome.error,
                result=outcome.data if outcome.ok else None,
            )
            if status != "succeeded":
                st["status"] = "error" if status == "failed" else "blocked"
        async with tenant_session(tenant.id) as session:
            row = await session.get(ToolCall, existing_call_id) if existing_call_id else None
            if row is None:
                row = ToolCall(
                    tenant_id=tenant.id,
                    run_id=run_id,
                    tool_use_id=tool_use_id,
                    source_id=source_id,
                    tool_name=name,
                    request_json=arguments,
                    requires_approval=False,
                )
                session.add(row)
            row.status = status
            row.response_json = redact(outcome.data) if outcome.ok else {"error": outcome.error}
            row.latency_ms = outcome.latency_ms
            row.error = outcome.error
        TOOL_CALLS.labels(tenant.slug, name, status).inc()
        if status == "failed":
            TOOL_FAILURES.labels(tenant.slug, name).inc()
        return block

    async def _record_pending(
        self,
        tenant: TenantRef,
        run_id: uuid.UUID,
        recorder: RunRecorder,
        tool_use_id: str,
        name: str,
        arguments: dict[str, Any],
        source_id: str,
        reason: str,
    ) -> uuid.UUID:
        async with recorder.step("approval_request", f"{name} awaiting approval") as st:
            st.update(tool=name, arguments=arguments, reason=reason, source_id=source_id, status="pending")
        async with tenant_session(tenant.id) as session:
            row = ToolCall(
                tenant_id=tenant.id,
                run_id=run_id,
                tool_use_id=tool_use_id,
                source_id=source_id,
                tool_name=name,
                request_json=arguments,
                status="pending_approval",
                requires_approval=True,
                approval_status="pending",
                error=None,
            )
            session.add(row)
            await session.flush()
            call_id = row.id
        TOOL_CALLS.labels(tenant.slug, name, "pending_approval").inc()
        return call_id

    # ================================================================== run state transitions
    async def _pause(self, tenant: TenantRef, run_id: uuid.UUID, state: dict[str, Any], t0: float) -> RunResult:
        segment_ms = int((time.perf_counter() - t0) * 1000)
        state["latency_ms"] += segment_ms
        AGENT_LATENCY.labels(tenant.slug).observe(segment_ms / 1000)
        async with tenant_session(tenant.id) as session:
            run = await session.get(AgentRun, run_id)
            run.status, run.state = "awaiting_approval", state
            self._apply_usage(run, state)
            pending_ids = [uuid.UUID(v) for v in state["pending"]["awaiting"].values()]
            calls = (await session.execute(select(ToolCall).where(ToolCall.id.in_(pending_ids)))).scalars().all()
            pending_actions = [
                {
                    "tool_call_id": c.id,
                    "tool": c.tool_name,
                    "arguments": c.request_json,
                    "reason": f"'{c.tool_name}' changes the customer's systems and requires human approval.",
                }
                for c in calls
            ]
            model = run.model
        return RunResult(
            run_id=run_id,
            tenant_id=tenant.id,
            status="awaiting_approval",
            model=model,
            pending_actions=pending_actions,
            request_id=state.get("request_id"),
            trace_id=state.get("trace_id"),
            latency_ms=state["latency_ms"],
            usage=state["usage"],
        )

    async def _finish(
        self,
        tenant: TenantRef,
        run_id: uuid.UUID,
        state: dict[str, Any],
        t0: float,
        status: str,
        answer: FinalAnswer | None = None,
        error: str | None = None,
        error_kind: str | None = None,
    ) -> RunResult:
        segment_ms = int((time.perf_counter() - t0) * 1000)
        state["latency_ms"] = state.get("latency_ms", 0) + segment_ms
        AGENT_LATENCY.labels(tenant.slug).observe(segment_ms / 1000)
        AGENT_REQUESTS.labels(tenant.slug, status).inc()
        if status == "failed":
            AGENT_ERRORS.labels(tenant.slug, error_kind or "unknown").inc()
        async with tenant_session(tenant.id) as session:
            run = await session.get(AgentRun, run_id)
            run.status = status
            run.response = answer.model_dump() if answer else None
            run.error = error
            run.state = state
            run.completed_at = utcnow()
            run.trace_id = run.trace_id or state.get("trace_id")
            self._apply_usage(run, state)
            model = run.model
        log.info(
            "agent run finished",
            extra={
                "status": status,
                "latency_ms": state["latency_ms"],
                "llm_calls": state.get("usage", {}).get("llm_calls", 0),
            },
        )
        return RunResult(
            run_id=run_id,
            tenant_id=tenant.id,
            status=status,
            model=model,
            answer=answer.model_dump() if answer else None,
            error=error,
            request_id=state.get("request_id"),
            trace_id=state.get("trace_id"),
            latency_ms=state["latency_ms"],
            usage=state.get("usage", {}),
        )

    @staticmethod
    def _apply_usage(run: AgentRun, state: dict[str, Any]) -> None:
        usage = state.get("usage", {})
        run.latency_ms = state.get("latency_ms")
        run.input_tokens = usage.get("input_tokens", 0)
        run.output_tokens = usage.get("output_tokens", 0)
        run.cache_read_tokens = usage.get("cache_read_tokens", 0)
        run.llm_calls = usage.get("llm_calls", 0)
        if state.get("trace_id") and not run.trace_id:
            run.trace_id = state["trace_id"]
