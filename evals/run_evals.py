"""Agent evaluation harness.

    python -m evals.run_evals --base-url http://localhost:8000 [--dataset evals/datasets/ops_agent.jsonl]

Runs every case through the real API (so it exercises retrieval, MCP tools, policy and
approvals exactly as production does), then grades the *process*, not just the prose:

* tool_recall        - did it call every expected tool?
* forbidden_tools    - did it call anything it must not (e.g. write tools)?
* fact_recall        - are the expected facts in the answer? (each fact may list alternatives)
* unsupported_ids    - identifiers in the answer (C-104, J-774, E-ACT-401, TCK-...) that appear
                       in no tool result, no retrieved context and not in the question -> hallucinations
* citation_errors    - findings citing source ids (K#/T#) that never existed in the run
* tool_success_rate  - executed tool calls that succeeded
* latency / tokens / llm_calls

Exit code is non-zero when the pass rate is below --min-pass-rate, so CI can gate on it.
"""

import argparse
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

ID_PATTERN = re.compile(r"\b(?:C|ACC|J|TCK)-\d{2,6}\b|\bE-[A-Z]{2,5}-\d{2,4}\b")


@dataclass
class CaseResult:
    id: str
    tenant: str
    passed: bool
    status: str
    tools_called: list[str]
    tool_recall: float
    forbidden_called: list[str]
    fact_recall: float
    missing_facts: list[str]
    unsupported_ids: list[str]
    citation_errors: list[str]
    tool_success_rate: float | None
    latency_ms: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    notes: list[str] = field(default_factory=list)
    run_id: str | None = None


def _answer_text(answer: dict | None) -> str:
    if not answer:
        return ""
    parts = [answer.get("summary", "")]
    parts += [f.get("statement", "") for f in answer.get("findings", [])]
    parts += answer.get("recommended_actions", [])
    return "\n".join(parts)


def _fact_found(fact: str | list[str], text: str) -> bool:
    options = fact if isinstance(fact, list) else [fact]
    return any(opt.lower() in text.lower() for opt in options)


class Evaluator:
    def __init__(self, base_url: str, email: str, password: str):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=180)
        resp = self.client.post("/api/auth/login", json={"email": email, "password": password})
        resp.raise_for_status()
        self.token = resp.json()["access_token"]

    def headers(self, tenant: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "X-Tenant-ID": tenant}

    def run_case(self, case: dict) -> CaseResult:
        tenant, notes = case["tenant"], []
        started = time.perf_counter()
        body = self.client.post("/api/agent/run", headers=self.headers(tenant), json={"message": case["input"]})
        body.raise_for_status()
        result = body.json()
        # Resolve approval gates the way the case prescribes.
        for _ in range(3):
            if result["status"] != "awaiting_approval":
                break
            decision = case.get("approval", "none")
            if decision == "none":
                notes.append("unexpected approval request (rejected by harness)")
                decision = "reject"
            for action in result["pending_actions"]:
                if case.get("expected_ticket_priority") and action["tool"] == "create_ticket":
                    got = action["arguments"].get("priority")
                    if got != case["expected_ticket_priority"]:
                        notes.append(f"ticket priority {got} != expected {case['expected_ticket_priority']}")
                resp = self.client.post(
                    f"/api/agent/{result['run_id']}/approve",
                    headers=self.headers(tenant),
                    json={"tool_call_id": action["tool_call_id"], "decision": decision, "comment": "eval harness"},
                )
                resp.raise_for_status()
                result = resp.json()
        latency_ms = int((time.perf_counter() - started) * 1000)
        trace = self.client.get(f"/api/runs/{result['run_id']}", headers=self.headers(tenant)).json()

        called = [c["tool_name"] for c in trace["tool_calls"]]
        expected = case.get("expected_tools", [])
        tool_recall = (len(set(expected) & set(called)) / len(expected)) if expected else 1.0
        forbidden = sorted(set(case.get("forbidden_tools", [])) & set(called))
        text = _answer_text(result.get("answer"))
        missing = [str(f) for f in case.get("expected_facts", []) if not _fact_found(f, text)]
        fact_recall = 1 - len(missing) / max(1, len(case.get("expected_facts", [])))

        evidence = json.dumps([c.get("response_json") for c in trace["tool_calls"]], default=str) + case["input"]
        for step in trace["steps"]:
            if step["step_type"] == "context_build":
                evidence += json.dumps(step["attributes"].get("selected", []))
        unsupported = sorted({i for i in ID_PATTERN.findall(text) if i not in evidence})
        final = next((s for s in trace["steps"] if s["step_type"] == "final_answer"), None)
        citation_errors = final["attributes"].get("unsupported_source_ids", []) if final else []
        executed = [c for c in trace["tool_calls"] if c["status"] in ("succeeded", "failed")]
        tool_success = (sum(c["status"] == "succeeded" for c in executed) / len(executed)) if executed else None

        if case.get("expect_followup") and not (result.get("answer") or {}).get("needs_human_followup"):
            notes.append("expected needs_human_followup=true")
        passed = (
            result["status"] == case.get("expect_status", "completed")
            and tool_recall == 1.0
            and not forbidden
            and not missing
            and not unsupported
            and not citation_errors
            and not any(n.startswith(("unexpected", "ticket priority", "expected")) for n in notes)
        )
        return CaseResult(
            id=case["id"], tenant=tenant, passed=passed, status=result["status"], tools_called=called,
            tool_recall=round(tool_recall, 3), forbidden_called=forbidden, fact_recall=round(fact_recall, 3),
            missing_facts=missing, unsupported_ids=unsupported, citation_errors=citation_errors,
            tool_success_rate=None if tool_success is None else round(tool_success, 3), latency_ms=latency_ms,
            llm_calls=trace["llm_calls"], input_tokens=trace["input_tokens"], output_tokens=trace["output_tokens"],
            notes=notes, run_id=result["run_id"],
        )  # fmt: skip


def summarize(results: list[CaseResult], model: str) -> dict:
    latencies = [r.latency_ms for r in results]
    success = [r.tool_success_rate for r in results if r.tool_success_rate is not None]
    return {
        "model": model,
        "cases": len(results),
        "pass_rate": round(sum(r.passed for r in results) / len(results), 3),
        "tool_selection_recall": round(statistics.mean(r.tool_recall for r in results), 3),
        "forbidden_tool_calls": sum(len(r.forbidden_called) for r in results),
        "fact_recall": round(statistics.mean(r.fact_recall for r in results), 3),
        "unsupported_identifiers": sum(len(r.unsupported_ids) for r in results),
        "citation_errors": sum(len(r.citation_errors) for r in results),
        "tool_success_rate": round(statistics.mean(success), 3) if success else None,
        "latency_ms_p50": int(statistics.median(latencies)),
        "latency_ms_max": max(latencies),
        "avg_llm_calls": round(statistics.mean(r.llm_calls for r in results), 2),
    }


def to_markdown(summary: dict, results: list[CaseResult]) -> str:
    lines = [
        f"# Eval results - {summary['model']} - {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        "| metric | value |",
        "|---|---|",
        *[f"| {k} | {v} |" for k, v in summary.items() if k != "model"],
        "",
        "| case | pass | tools called | tool recall | fact recall | unsupported ids | latency ms | notes |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        notes = "; ".join(r.notes + ([f"missing: {', '.join(r.missing_facts)}"] if r.missing_facts else []))
        lines.append(
            f"| {r.id} | {'PASS' if r.passed else 'FAIL'} | {', '.join(r.tools_called) or '-'} | {r.tool_recall} "
            f"| {r.fact_recall} | {', '.join(r.unsupported_ids) or '-'} | {r.latency_ms} | {notes or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", default="evals/datasets/ops_agent.jsonl")
    parser.add_argument("--email", default="admin@tenantpilot.dev")
    parser.add_argument("--password", default="tenantpilot-demo")
    parser.add_argument("--out-dir", default="evals/results")
    parser.add_argument("--min-pass-rate", type=float, default=0.8)
    args = parser.parse_args()

    cases = [json.loads(line) for line in Path(args.dataset).read_text().splitlines() if line.strip()]
    evaluator = Evaluator(args.base_url, args.email, args.password)
    results = []
    for case in cases:
        result = evaluator.run_case(case)
        results.append(result)
        print(f"{'PASS' if result.passed else 'FAIL'}  {case['id']:<34} tools={result.tools_called} "
              f"facts={result.fact_recall} {result.latency_ms}ms {'; '.join(result.notes)}")  # fmt: skip
    model = evaluator.client.get(f"/api/tenants/{cases[0]['tenant']}", headers=evaluator.headers(cases[0]["tenant"]))
    model_name = model.json().get("config", {}).get("model_name", "unknown")
    summary = summarize(results, model_name)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {"summary": summary, "results": [asdict(r) for r in results]}
    (out / f"{stamp}-{model_name}.json").write_text(json.dumps(payload, indent=2))
    (out / "latest.json").write_text(json.dumps(payload, indent=2))
    (out / "latest.md").write_text(to_markdown(summary, results))
    print(json.dumps(summary, indent=2))
    return 0 if summary["pass_rate"] >= args.min_pass_rate else 1


if __name__ == "__main__":
    sys.exit(main())
