"""Structured JSON logging with request/tenant correlation and secret redaction.

Every log line carries request_id / tenant_id / run_id / trace_id (when known) so a
single agent run can be followed across the API, the MCP server and the worker.
Secrets never reach log sinks: known-sensitive keys are masked and token-shaped values
are scrubbed from messages.
"""

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)

SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "secret",
    "api_key",
    "apikey",
    "x-api-key",
    "authorization",
    "token",
    "access_token",
    "credential",
    "credentials",
    "dsn",
    "jwt",
    "cookie",
    "ciphertext",
}
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),  # Anthropic keys
    re.compile(r"(?i)bearer\s+[A-Za-z0-9_\-\.=]{12,}"),  # bearer tokens / JWTs
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # bare JWTs
    re.compile(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:\s/]+:)[^@\s]+@"),  # DSN passwords
]
REDACTED = "***redacted***"


def redact_text(text: str) -> str:
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)
        else:
            text = pattern.sub(REDACTED, text)
    return text


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively mask sensitive keys and scrub token-shaped strings."""
    if _depth > 8:
        return value
    if isinstance(value, dict):
        return {
            k: (REDACTED if isinstance(k, str) and k.lower() in SENSITIVE_KEYS else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "message": redact_text(record.getMessage()),
        }
        for key, var in (("request_id", request_id_var), ("tenant_id", tenant_id_var), ("run_id", run_id_var)):
            if (val := var.get()) is not None:
                payload[key] = val
        trace_id = _current_trace_id()
        if trace_id:
            payload["trace_id"] = trace_id
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED and not k.startswith("_")}
        if extras:
            payload.update(redact(extras))
        if record.exc_info:
            payload["exc"] = redact_text(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rid = request_id_var.get()
        tid = tenant_id_var.get()
        prefix = " ".join(p for p in (f"req={rid}" if rid else "", f"tenant={tid}" if tid else "") if p)
        base = f"{record.levelname:<7} {record.name}: {redact_text(record.getMessage())}"
        return f"{base} [{prefix}]" if prefix else base


def _current_trace_id() -> str | None:
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except Exception:  # pragma: no cover - telemetry must never break logging
        return None


def configure_logging(service: str, level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service) if fmt == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Third-party request logs are noisy and can include URLs with query strings.
    for noisy in (
        "httpx",
        "httpx2",
        "httpcore",
        "httpcore2",
        "uvicorn.access",
        "mcp.client.streamable_http",
        "alembic.runtime.plugins",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
