"""Prometheus metrics (scraped from /metrics; dashboards in deploy/observability/grafana).

Label cardinality is kept bounded: tenant slugs (tens, not millions), tool names
(a fixed catalog) and a small status vocabulary. Never label by user, run or request id.
"""

from prometheus_client import Counter, Histogram

LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64)

AGENT_REQUESTS = Counter("agent_requests_total", "Agent runs finished, by final status", ["tenant", "status"])
AGENT_ERRORS = Counter("agent_errors_total", "Agent runs that failed, by error kind", ["tenant", "kind"])
AGENT_LATENCY = Histogram(
    "agent_latency_seconds", "Wall-clock latency of an agent run segment", ["tenant"], buckets=LATENCY_BUCKETS
)
TOOL_CALLS = Counter("tool_calls_total", "Tool calls by outcome", ["tenant", "tool", "status"])
TOOL_FAILURES = Counter("tool_failures_total", "Tool calls that errored", ["tenant", "tool"])
RETRIEVAL_LATENCY = Histogram(
    "retrieval_latency_seconds", "Vector retrieval latency", ["tenant"], buckets=LATENCY_BUCKETS
)
LLM_TOKENS = Counter("llm_tokens_total", "LLM tokens consumed", ["tenant", "model", "direction"])
APPROVALS = Counter("agent_approvals_total", "Human decisions on write actions", ["tenant", "decision"])
CONNECTOR_REQUESTS = Counter(
    "connector_requests_total", "Outbound connector calls (including retries)", ["connector_type", "outcome"]
)
RATE_LIMITED = Counter("agent_rate_limited_total", "Agent requests rejected by the rate limiter", ["tenant"])
