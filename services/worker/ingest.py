"""Document ingestion: extract text -> chunk -> embed -> store in pgvector.

Runs in the background worker (RQ) so uploads return immediately; status moves
queued -> processing -> indexed | failed and is visible in the UI. Re-running a job is
safe (idempotent): existing chunks for the document are replaced.
"""

import io
import logging
import time
import uuid

from sqlalchemy import delete, select

from packages.core.db import tenant_session
from packages.core.logging import tenant_id_var
from packages.core.models import Document, DocumentChunk, utcnow
from packages.core.storage import get_object_store
from packages.core.telemetry import span
from packages.knowledge.chunking import chunk_document
from packages.knowledge.embeddings import get_embedder

log = logging.getLogger(__name__)
TEXT_EXTENSIONS = (".md", ".markdown", ".txt", ".csv", ".json", ".log", ".yaml", ".yml")


class UnsupportedDocumentError(Exception):
    pass


def extract_text(data: bytes, filename: str, content_type: str) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf") or content_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    if lower.endswith(TEXT_EXTENSIONS) or content_type.startswith("text/"):
        return data.decode("utf-8", errors="replace")
    raise UnsupportedDocumentError(f"unsupported file type: {filename}")


async def ingest_document(tenant_id: uuid.UUID, document_id: uuid.UUID) -> int:
    token = tenant_id_var.set(str(tenant_id))
    started = time.perf_counter()
    try:
        with span("ingest.document", **{"tenant.id": str(tenant_id), "document.id": str(document_id)}):
            async with tenant_session(tenant_id) as session:
                doc = (await session.execute(select(Document).where(Document.id == document_id))).scalar_one()
                doc.status = "processing"
                storage_key, filename, content_type = doc.storage_key, doc.filename, doc.content_type
            try:
                data = await get_object_store().get(storage_key)
                text = extract_text(data, filename, content_type)
                chunks = chunk_document(text, filename=filename)
                if not chunks:
                    raise UnsupportedDocumentError("no extractable text")
                vectors = get_embedder().embed([c.content for c in chunks])
                async with tenant_session(tenant_id) as session:
                    await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
                    session.add_all(
                        DocumentChunk(
                            tenant_id=tenant_id,
                            document_id=document_id,
                            chunk_index=c.index,
                            content=c.content,
                            content_hash=c.content_hash,
                            token_count=c.token_count,
                            embedding=vec,
                            metadata_=c.metadata,
                        )
                        for c, vec in zip(chunks, vectors, strict=True)
                    )
                    doc = (await session.execute(select(Document).where(Document.id == document_id))).scalar_one()
                    doc.status, doc.chunk_count, doc.indexed_at, doc.error = "indexed", len(chunks), utcnow(), None
            except Exception as exc:
                log.exception("ingestion failed", extra={"document_id": str(document_id)})
                async with tenant_session(tenant_id) as session:
                    doc = (await session.execute(select(Document).where(Document.id == document_id))).scalar_one()
                    doc.status, doc.error = "failed", f"{type(exc).__name__}: {exc}"[:500]
                raise
        log.info(
            "document indexed",
            extra={
                "document_id": str(document_id),
                "chunks": len(chunks),
                "duration_ms": int((time.perf_counter() - started) * 1000),
            },
        )
        return len(chunks)
    finally:
        tenant_id_var.reset(token)
