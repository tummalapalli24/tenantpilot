"""Records each agent step twice: as an OpenTelemetry span (for Jaeger/Tempo) and as a
``run_steps`` row (for the TenantPilot trace screen, which needs no tracing backend).

Each step is persisted in its own short transaction, so a crash mid-run still leaves
a readable trace of everything that happened before it.
"""

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from packages.core.db import tenant_session
from packages.core.logging import redact
from packages.core.models import RunStep, utcnow
from packages.core.telemetry import span

_SPAN_SAFE = (str, int, float, bool)


class RunRecorder:
    def __init__(self, tenant_id: uuid.UUID, run_id: uuid.UUID, seq_start: int = 0):
        self.tenant_id = tenant_id
        self.run_id = run_id
        self.seq = seq_start

    @classmethod
    async def resume(cls, tenant_id: uuid.UUID, run_id: uuid.UUID) -> "RunRecorder":
        async with tenant_session(tenant_id) as session:
            last = await session.scalar(select(func.max(RunStep.seq)).where(RunStep.run_id == run_id))
        return cls(tenant_id, run_id, seq_start=last or 0)

    @asynccontextmanager
    async def step(self, step_type: str, name: str, **initial: Any) -> AsyncIterator[dict[str, Any]]:
        """Yields a mutable dict of attributes. Set ``attrs["status"] = "error"`` to mark a
        handled failure; an exception escaping the block marks the step as errored too."""
        self.seq += 1
        seq = self.seq
        attrs: dict[str, Any] = dict(initial)
        started_at: datetime = utcnow()
        t0 = time.perf_counter()
        with span(f"agent.{step_type}", **{"tenantpilot.step": name, "tenantpilot.run_id": str(self.run_id)}) as sp:
            try:
                yield attrs
            except Exception as exc:
                attrs["status"] = "error"
                attrs.setdefault("error", f"{type(exc).__name__}: {exc}"[:500])
                raise
            finally:
                status = str(attrs.pop("status", "ok"))
                measured = attrs.pop("duration_ms", None)  # work timed elsewhere (e.g. a batched MCP call)
                latency_ms = int(measured) if measured is not None else int((time.perf_counter() - t0) * 1000)
                for key, value in attrs.items():
                    if isinstance(value, _SPAN_SAFE):
                        sp.set_attribute(f"tenantpilot.{key}", value)
                async with tenant_session(self.tenant_id) as session:
                    session.add(
                        RunStep(
                            tenant_id=self.tenant_id,
                            run_id=self.run_id,
                            seq=seq,
                            step_type=step_type,
                            name=name,
                            status=status,
                            started_at=started_at,
                            latency_ms=latency_ms,
                            attributes=redact(attrs),
                        )
                    )
