"""RQ job entrypoints. RQ calls plain (sync) functions, so each job drives its async
implementation with asyncio.run() and disposes the engine afterwards (each job may run
in a fresh forked work-horse process with its own event loop).

The forked work-horse exits right after the job, before the BatchSpanProcessor's
background thread would export, so spans are flushed explicitly at the end of each job.
"""

import asyncio
import uuid

from opentelemetry import trace

from packages.core.db import dispose_engine
from services.worker.ingest import ingest_document


def ingest_document_job(tenant_id: str, document_id: str) -> int:
    async def _run() -> int:
        try:
            return await ingest_document(uuid.UUID(tenant_id), uuid.UUID(document_id))
        finally:
            await dispose_engine()

    try:
        return asyncio.run(_run())
    finally:
        provider = trace.get_tracer_provider()
        if hasattr(provider, "force_flush"):
            provider.force_flush(timeout_millis=5000)
