"""Tenant-scoped vector retrieval over pgvector.

Isolation is enforced twice: the query filters ``tenant_id`` explicitly, and the
session's Row-Level Security policy would hide other tenants' chunks even if that
filter were ever removed. ``tests/integration/test_tenant_isolation.py`` checks both.
"""

import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.metrics import RETRIEVAL_LATENCY
from packages.core.models import Document, DocumentChunk
from packages.knowledge.embeddings import get_embedder


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document: str
    section: str | None
    content: str
    content_hash: str
    token_count: int
    vector_score: float


async def search_chunks(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    query: str,
    top_k: int = 8,
    tenant_label: str = "unknown",
) -> list[RetrievedChunk]:
    started = time.perf_counter()
    query_vec = get_embedder().embed([query])[0]
    # With a filtered HNSW scan, pgvector >= 0.8 keeps scanning until it finds enough rows
    # that pass the tenant filter instead of returning a short result list.
    await session.execute(text("SET LOCAL hnsw.iterative_scan = 'relaxed_order'"))
    distance = DocumentChunk.embedding.cosine_distance(query_vec)
    stmt = (
        select(
            DocumentChunk.id,
            DocumentChunk.document_id,
            Document.filename,
            DocumentChunk.metadata_,
            DocumentChunk.content,
            DocumentChunk.content_hash,
            DocumentChunk.token_count,
            (1 - distance).label("score"),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(DocumentChunk.tenant_id == tenant_id)
        .order_by(distance)
        .limit(top_k)
    )
    rows = (await session.execute(stmt)).all()
    RETRIEVAL_LATENCY.labels(tenant_label).observe(time.perf_counter() - started)
    return [
        RetrievedChunk(
            chunk_id=str(r.id),
            document_id=str(r.document_id),
            document=r.filename,
            section=(r.metadata_ or {}).get("section"),
            content=r.content,
            content_hash=r.content_hash,
            token_count=r.token_count,
            vector_score=round(float(r.score), 4),
        )
        for r in rows
    ]
