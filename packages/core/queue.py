"""Background job enqueueing (Redis + RQ).

``INGEST_MODE=inline`` runs the job in-process instead - handy for tests and for running
the API without a worker. The upload endpoint is identical either way.
"""

import asyncio
import uuid

from redis import Redis
from rq import Queue, Retry

from packages.core.config import get_settings

INGEST_QUEUE = "ingest"


def get_queue(name: str = INGEST_QUEUE) -> Queue:
    return Queue(name, connection=Redis.from_url(get_settings().redis_url))


async def enqueue_ingest(tenant_id: uuid.UUID, document_id: uuid.UUID) -> str:
    if get_settings().ingest_mode == "inline":
        from services.worker.ingest import ingest_document

        await ingest_document(tenant_id, document_id)
        return "inline"

    def _enqueue() -> str:
        job = get_queue().enqueue(
            "services.worker.jobs.ingest_document_job",
            str(tenant_id),
            str(document_id),
            job_timeout=300,
            retry=Retry(max=2, interval=[5, 30]),
            description=f"ingest document {document_id}",
        )
        return job.id

    return await asyncio.to_thread(_enqueue)
