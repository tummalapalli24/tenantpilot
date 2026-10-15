"""OpenTelemetry tracing setup shared by every service.

Spans exported via OTLP/HTTP when OTEL_EXPORTER_OTLP_ENDPOINT is set (Jaeger in the
docker-compose `observability` profile). Trace context crosses service boundaries by:

* W3C ``traceparent`` headers on our own httpx calls (connector -> customer API), and
* the MCP SDK's native propagation through the JSON-RPC ``_meta`` field (API -> MCP server),

so one agent run shows up as a single distributed trace.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Span, Status, StatusCode

from packages.core.config import get_settings

log = logging.getLogger(__name__)
_configured = False


def setup_telemetry(service_name: str) -> None:
    global _configured
    if _configured:
        return
    settings = get_settings()
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name, "deployment.environment": settings.environment})
    )
    if settings.otel_exporter_otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        endpoint = settings.otel_exporter_otlp_endpoint.rstrip("/") + "/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        log.info("OTLP trace export enabled", extra={"otlp_endpoint": endpoint})
    if settings.otel_console_exporter:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:  # pragma: no cover
        log.warning("httpx instrumentation unavailable")
    _configured = True


def tracer() -> trace.Tracer:
    return trace.get_tracer("tenantpilot")


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Start a span; exceptions mark it as errored and re-raise."""
    with tracer().start_as_current_span(name) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.record_exception(exc)
            current.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            raise
