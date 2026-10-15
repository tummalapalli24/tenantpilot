"""Deterministic, rule-based stand-in for an LLM ("offline-heuristic").

Why it exists: CI, local demos and evaluation baselines must run without an API key
and produce identical results every time. It speaks exactly the same protocol as the
Claude provider - it reads the same transcript (context, tool_use, tool_result blocks)
and emits tool_use blocks or a final JSON answer - so every other part of the platform
(policy, approvals, MCP calls, tracing, evals) is exercised for real.

It is a scripted planner, not a language model: it follows a fixed investigation
playbook (customer -> open jobs -> knowledge for fault codes -> ticket if asked).
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from packages.context.intent import Intent, classify_intent
from packages.core.tokens import estimate_tokens
from packages.knowledge.embeddings import terms
from services.agent.llm.base import LLMResponse, LLMUsage, ModelConfig, ToolSpec

_REQUEST = re.compile(r"<request>\n(.*?)\n</request>", re.DOTALL)
_SOURCE = re.compile(r'<source id="(K\d+)" document="([^"]*)" section="([^"]*)">\n(.*?)\n</source>', re.DOTALL)
_STEP = re.compile(r"^\s*\d+[.)]\s+(.+)$", re.MULTILINE)
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
CRITICAL_CODES = {"E-CMP-503"}
PROBLEM_STATES = {"FAILED", "ON_HOLD"}


@dataclass
class Call:
    name: str
    input: dict[str, Any]
    source_id: str | None = None
    ok: bool | None = None
    result: Any = None
    error: str | None = None


@dataclass
class Observation:
    request: str
    intent: Intent
    knowledge: list[tuple[str, str, str]] = field(default_factory=list)  # (source_id, section, content)
    calls: list[Call] = field(default_factory=list)

    def last(self, name: str) -> Call | None:
        return next((c for c in reversed(self.calls) if c.name == name), None)

    available: set[str] = field(default_factory=set)

    def called(self, name: str) -> bool:
        return any(c.name == name for c in self.calls)

    def tools_include(self, name: str) -> bool:
        return name in self.available


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def observe(messages: list[dict[str, Any]]) -> Observation:
    first = _content_text(messages[0]["content"]) if messages else ""
    match = _REQUEST.search(first)
    request = match.group(1).strip() if match else first.strip()
    obs = Observation(request=request, intent=classify_intent(request))
    obs.knowledge = [(sid, section, text) for sid, _doc, section, text in _SOURCE.findall(first)]
    by_id: dict[str, Call] = {}
    for message in messages[1:]:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                call = Call(name=block["name"], input=block.get("input") or {})
                by_id[block["id"]] = call
                obs.calls.append(call)
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in by_id:
                call = by_id[block["tool_use_id"]]
                try:
                    payload = json.loads(_content_text(block.get("content")))
                except json.JSONDecodeError:
                    payload = {"ok": False, "error": _content_text(block.get("content"))}
                call.source_id = payload.get("source_id")
                call.ok = bool(payload.get("ok")) and not block.get("is_error")
                call.result = payload.get("result")
                call.error = payload.get("error")
    return obs


def _problem_jobs(obs: Observation) -> list[dict[str, Any]]:
    jobs_call = obs.last("list_open_jobs")
    if not jobs_call or not jobs_call.ok or not isinstance(jobs_call.result, dict):
        return []
    return [
        j
        for j in jobs_call.result.get("jobs", [])
        if j.get("error_code") or str(j.get("status", "")).upper() in PROBLEM_STATES
    ]


def _evidence(obs: Observation) -> list[tuple[str, str]]:
    items = [(sid, text) for sid, _section, text in obs.knowledge]
    for call in obs.calls:
        if call.name == "search_knowledge" and call.ok and isinstance(call.result, dict):
            items += [(call.source_id or "?", hit.get("content", "")) for hit in call.result.get("hits", [])]
    return items


def _codes(obs: Observation) -> list[str]:
    codes = list(obs.intent.error_codes)
    codes += [j["error_code"] for j in _problem_jobs(obs) if j.get("error_code")]
    return list(dict.fromkeys(codes))


def _meaning(code: str, text: str) -> str | None:
    match = re.search(re.escape(code) + r"\s*[:\-—]\s*([^\]\n]+)", text)
    return match.group(1).strip().rstrip(".") if match else None


def _body_sentences(text: str, n: int = 2) -> str:
    body = re.sub(r"^\[[^\]]*\]\n", "", text).strip()
    body = re.sub(r"^\s*\d+[.)]\s+.*$", "", body, flags=re.MULTILINE)  # numbered steps go to actions
    sentences = [s.strip() for s in _SENTENCES.split(" ".join(body.split())) if s.strip()]
    return " ".join(sentences[:n])


def _best_passages(evidence: list[tuple[str, str]], question: str, n: int = 2) -> list[tuple[str, str]]:
    """Extractive QA baseline: rank every line/sentence of the evidence by term overlap
    with the question (ties -> earlier evidence wins) and return the top passages."""
    wanted = set(terms(question))
    scored: list[tuple[int, int, str, str]] = []
    for order, (sid, text) in enumerate(evidence):
        body = re.sub(r"^\[[^\]]*\]\n", "", text)
        for line in (ln.strip(" -*") for ln in body.splitlines()):
            units = _SENTENCES.split(line) if len(line) > 300 else [line]
            for unit in units:
                overlap = len(wanted & set(terms(unit)))
                if overlap and len(unit) > 20:
                    scored.append((overlap, -order, sid, unit.replace("**", "")))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [(sid, unit) for _, _, sid, unit in scored[:n]]


class OfflineHeuristicProvider:
    name = "offline"

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        config: ModelConfig,
        output_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        obs = observe(messages)
        available = {t.name for t in tools}
        obs.available = available
        action = self._next_action(obs, available)
        if action is not None:
            name, arguments, preface = action
            content = [
                {"type": "text", "text": preface},
                {
                    "type": "tool_use",
                    "id": f"toolu_offline_{len(messages):02d}_{name}",
                    "name": name,
                    "input": arguments,
                },
            ]
            stop_reason = "tool_use"
        else:
            content = [{"type": "text", "text": json.dumps(self._final_answer(obs))}]
            stop_reason = "end_turn"
        prompt_tokens = estimate_tokens(system + json.dumps(messages, default=str))
        return LLMResponse(
            content=content,
            stop_reason=stop_reason,
            usage=LLMUsage(input_tokens=prompt_tokens, output_tokens=estimate_tokens(json.dumps(content))),
            model=config.model,
            notes=["offline heuristic planner (deterministic, no LLM call)"],
        )

    # ------------------------------------------------------------------ planning
    def _next_action(self, obs: Observation, available: set[str]) -> tuple[str, dict, str] | None:
        cid = obs.intent.customer_ids[0] if obs.intent.customer_ids else None
        customer_call = obs.last("get_customer")
        if cid and "get_customer" in available and not obs.called("get_customer"):
            return "get_customer", {"customer_id": cid}, f"Looking up customer {cid}."
        customer_ok = bool(customer_call and customer_call.ok)
        investigating = obs.intent.kind in ("troubleshooting", "action_request")
        if cid and customer_ok and investigating and "list_open_jobs" in available and not obs.called("list_open_jobs"):
            return "list_open_jobs", {"customer_id": cid}, f"Checking open jobs for {cid}."
        searches = [c for c in obs.calls if c.name == "search_knowledge"]
        if "search_knowledge" in available and len(searches) < 2:
            evidence = _evidence(obs)
            evidence_text = " ".join(t for _, t in evidence)
            searched = " ".join(str(c.input.get("query", "")) for c in searches)
            for code in _codes(obs):
                # Found the code but no resolution procedure yet -> look it up explicitly.
                has_steps = any(code in t and _STEP.search(t) for _, t in evidence)
                if not has_steps and code not in searched:
                    return (
                        "search_knowledge",
                        {"query": f"{code} cause and resolution steps", "top_k": 4},
                        f"Searching the knowledge base for {code}.",
                    )
            if not evidence_text and not searches:
                return "search_knowledge", {"query": obs.request, "top_k": 4}, "Searching the knowledge base."
        if (
            obs.intent.wants_ticket
            and cid
            and customer_ok
            and "create_ticket" in available
            and not obs.called("create_ticket")
        ):
            return "create_ticket", self._ticket_args(obs, cid), f"Requesting a support ticket for {cid}."
        return None

    def _ticket_args(self, obs: Observation, cid: str) -> dict[str, Any]:
        jobs = _problem_jobs(obs)
        codes = _codes(obs)
        evidence = " ".join(t for _, t in _evidence(obs))
        if obs.intent.urgency == "critical" or any(c in CRITICAL_CODES for c in codes):
            priority = "critical"
        elif obs.intent.urgency == "high" or any(str(j.get("status")).upper() == "FAILED" for j in jobs):
            priority = "high"
        else:
            priority = "medium"
        if jobs:
            job = jobs[0]
            meaning = _meaning(job.get("error_code") or "", evidence) if job.get("error_code") else None
            summary = (
                f"{job.get('job_type') or 'Job'} {job['job_id']} is {job.get('status')}"
                + (
                    f" with fault {job['error_code']}" + (f" ({meaning})" if meaning else "")
                    if job.get("error_code")
                    else ""
                )
                + f". {str(job.get('notes') or '')[:200]} Raised by TenantPilot agent."
            )
        else:
            summary = f"Customer {cid} reported: {obs.request[:300]} Raised by TenantPilot agent."
        return {"customer_id": cid, "summary": summary[:490], "priority": priority}

    # ------------------------------------------------------------------ answer
    def _final_answer(self, obs: Observation) -> dict[str, Any]:
        findings: list[dict[str, Any]] = []
        actions: list[str] = []
        followup = False
        summary_parts: list[str] = []
        evidence = _evidence(obs)

        customer_call = obs.last("get_customer")
        customer = customer_call.result if customer_call and customer_call.ok else None
        if customer:
            findings.append(
                {
                    "statement": f"{customer.get('name')} ({customer.get('customer_id')}) is "
                    f"{str(customer.get('status') or 'unknown').lower()}"
                    + (f" on the {customer.get('tier')} plan" if customer.get("tier") else "")
                    + (f" in {customer.get('region')}" if customer.get("region") else "")
                    + ".",
                    "source_ids": [customer_call.source_id],
                }
            )
            if customer.get("notes"):
                findings.append(
                    {"statement": f"Account notes: {customer['notes']}", "source_ids": [customer_call.source_id]}
                )
        elif customer_call:
            followup = True
            findings.append(
                {"statement": f"Customer lookup failed: {customer_call.error}", "source_ids": [customer_call.source_id]}
            )
            summary_parts.append(f"I could not retrieve the customer record ({customer_call.error}).")
            actions.append("Verify the customer ID, or retry once the customer's system is reachable.")

        jobs_call = obs.last("list_open_jobs")
        problem_jobs = _problem_jobs(obs)
        for job in problem_jobs:
            findings.append(
                {
                    "statement": f"Job {job['job_id']} ({job.get('job_type')}) is {job.get('status')}"
                    + (f" with fault {job['error_code']}" if job.get("error_code") else "")
                    + (f": {job['notes']}" if job.get("notes") else "."),
                    "source_ids": [jobs_call.source_id] if jobs_call else [],
                }
            )
        if jobs_call and jobs_call.ok and not problem_jobs:
            total = (jobs_call.result or {}).get("total", 0)
            findings.append(
                {
                    "statement": f"None of the {total} open jobs is failed or blocked.",
                    "source_ids": [jobs_call.source_id],
                }
            )
        if jobs_call and not jobs_call.ok:
            followup = True
            findings.append({"statement": f"Job lookup failed: {jobs_call.error}", "source_ids": [jobs_call.source_id]})

        meanings: dict[str, str] = {}
        for code in _codes(obs):
            matches = [(sid, text) for sid, text in evidence if code in text]
            if not matches:
                followup = True
                findings.append(
                    {"statement": f"No knowledge-base guidance was found for fault {code}.", "source_ids": []}
                )
                continue
            sid, text = matches[0]
            meaning = next((m for m in (_meaning(code, t) for _, t in matches) if m), None)
            if meaning:
                meanings[code] = meaning
            findings.append({"statement": f"Knowledge base on {code}: {_body_sentences(text)}", "source_ids": [sid]})
            step_sources = [(s_id, _STEP.findall(t)) for s_id, t in matches if _STEP.search(t)]
            for s_id, steps in step_sources[:1]:
                actions += [s.strip() for s in steps][:5]
                if s_id != sid:
                    findings.append(
                        {"statement": f"Resolution procedure for {code} ({len(steps)} steps).", "source_ids": [s_id]}
                    )
        passages = _best_passages(evidence, obs.request) if not _codes(obs) else []
        for sid, passage in passages:
            findings.append({"statement": passage, "source_ids": [sid]})
        if passages:
            top_text = next(text for sid, text in evidence if sid == passages[0][0])
            actions += [s.strip() for s in _STEP.findall(top_text)][:4]

        ticket_call = obs.last("create_ticket")
        if ticket_call and ticket_call.ok and isinstance(ticket_call.result, dict):
            t = ticket_call.result
            findings.append(
                {
                    "statement": f"Created ticket {t.get('ticket_id')} ({t.get('priority')}) for "
                    f"{t.get('customer_id')}.",
                    "source_ids": [ticket_call.source_id],
                }
            )
        elif ticket_call:
            followup = True
            findings.append(
                {
                    "statement": f"The ticket was not created: {ticket_call.error}",
                    "source_ids": [ticket_call.source_id] if ticket_call.source_id else [],
                }
            )
            actions.append("Create the ticket manually if it is still needed.")

        if obs.intent.wants_ticket and not ticket_call and not obs.tools_include("create_ticket"):
            followup = True
            findings.append(
                {
                    "statement": "Ticket creation is not enabled for this workspace, so no ticket was created.",
                    "source_ids": [],
                }
            )
            actions.append("Escalate through the documented channel for this customer instead of a ticket.")

        # summary
        if customer and problem_jobs:
            job = problem_jobs[0]
            code = job.get("error_code")
            detail = f" due to {code}" + (f" ({meanings[code].lower()})" if code in meanings else "") if code else ""
            summary_parts.insert(
                0,
                f"{customer.get('name')} ({customer.get('customer_id')}) has "
                f"{len(problem_jobs)} problem job(s): {job.get('job_type')} job {job['job_id']} "
                f"is {job.get('status')}{detail}.",
            )
        elif customer:
            jobs_note = " with no failed or blocked jobs found" if jobs_call and jobs_call.ok else ""
            summary_parts.insert(
                0,
                f"{customer.get('name')} ({customer.get('customer_id')}) is "
                f"{str(customer.get('status') or 'unknown').lower()}{jobs_note}.",
            )
        elif not customer_call and passages:
            summary_parts.insert(0, " ".join(p for _, p in passages))
        if actions:
            summary_parts.append(f"Recommended next step: {actions[0].rstrip('.')}.")
        if ticket_call and ticket_call.ok and isinstance(ticket_call.result, dict):
            summary_parts.append(f"Ticket {ticket_call.result.get('ticket_id')} was created.")
        if not summary_parts:
            summary_parts.append("I did not find enough information to answer this confidently.")
            followup = True

        confident_codes = bool(_codes(obs)) and all(c in meanings for c in _codes(obs))
        confidence = (
            "high"
            if customer and problem_jobs and confident_codes
            else "medium"
            if (customer or evidence) and not followup
            else "low"
        )
        for f in findings:
            f["source_ids"] = [s for s in f["source_ids"] if s]
        return {
            "summary": " ".join(summary_parts),
            "findings": findings,
            "recommended_actions": list(dict.fromkeys(actions))[:6],
            "confidence": confidence,
            "needs_human_followup": followup,
        }
