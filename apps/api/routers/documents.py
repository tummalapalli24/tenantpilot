"""Knowledge documents: upload -> object store -> background indexing; retrieval playground."""

import uuid
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from sqlalchemy import select

from apps.api.deps import TenantContext, tenant_scope
from packages.core.audit import record_audit
from packages.core.db import tenant_session
from packages.core.models import Document
from packages.core.queue import enqueue_ingest
from packages.core.storage import get_object_store
from packages.knowledge.retriever import search_chunks
from packages.schemas.api import DocumentOut, KnowledgeSearchHit

router = APIRouter(prefix="/api/tenants/{tenant_id}", tags=["knowledge"])
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf", ".csv", ".json"}


@router.post("/documents", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
async def upload_document(
    file: UploadFile, ctx: TenantContext = Depends(tenant_scope("documents:write"))
) -> DocumentOut:
    filename = PurePath(file.filename or "upload.txt").name  # drop any client-supplied path
    if PurePath(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"allowed types: {', '.join(sorted(ALLOWED_EXTENSIONS))}"
        )
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file exceeds 5 MB")
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "file is empty")
    doc_id = uuid.uuid4()
    key = f"{ctx.tenant.id}/{doc_id}/{filename}"
    content_type = file.content_type or "application/octet-stream"
    await get_object_store().put(key, data, content_type)
    async with tenant_session(ctx.tenant.id) as session:
        doc = Document(
            id=doc_id,
            tenant_id=ctx.tenant.id,
            filename=filename,
            source="upload",
            content_type=content_type,
            size_bytes=len(data),
            storage_key=key,
            status="queued",
        )
        session.add(doc)
        await record_audit(
            session,
            tenant_id=ctx.tenant.id,
            actor_id=ctx.principal.user_id,
            actor_email=ctx.principal.email,
            action="document.upload",
            resource_type="document",
            resource_id=str(doc_id),
            details={"filename": filename, "bytes": len(data)},
        )
    await enqueue_ingest(ctx.tenant.id, doc_id)
    async with tenant_session(ctx.tenant.id) as session:
        return DocumentOut.model_validate(await session.get(Document, doc_id))


@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(ctx: TenantContext = Depends(tenant_scope("tenants:read"))) -> list[DocumentOut]:
    async with tenant_session(ctx.tenant.id) as session:
        rows = (await session.execute(select(Document).order_by(Document.created_at.desc()))).scalars()
        return [DocumentOut.model_validate(r) for r in rows]


@router.get("/knowledge/search", response_model=list[KnowledgeSearchHit])
async def search_knowledge(
    q: str = Query(min_length=2, max_length=500),
    k: int = Query(5, ge=1, le=20),
    ctx: TenantContext = Depends(tenant_scope("runs:read")),
) -> list[KnowledgeSearchHit]:
    """Retrieval playground: see exactly what the retriever returns for a query."""
    async with tenant_session(ctx.tenant.id) as session:
        chunks = await search_chunks(session, ctx.tenant.id, q, top_k=k, tenant_label=ctx.tenant.slug)
    return [
        KnowledgeSearchHit(
            chunk_id=c.chunk_id, document=c.document, section=c.section, score=c.vector_score, content=c.content
        )
        for c in chunks
    ]
