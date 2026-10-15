"""Knowledge connector: the tenant's indexed documents, exposed as an operation."""

import time
import uuid
from typing import Any

from sqlalchemy import func, select

from packages.connectors.base import Connector, ConnectorError, HealthResult
from packages.core.db import tenant_session
from packages.core.models import DocumentChunk
from packages.knowledge.retriever import search_chunks


class KnowledgeConnector(Connector):
    connector_type = "knowledge"

    def __init__(self, tenant_id: uuid.UUID, tenant_label: str = "unknown"):
        super().__init__("knowledge-base")
        self.tenant_id = tenant_id
        self.tenant_label = tenant_label

    def supports(self, operation: str) -> bool:
        return operation == "search_knowledge"

    async def health_check(self) -> HealthResult:
        started = time.perf_counter()
        async with tenant_session(self.tenant_id) as session:
            count = await session.scalar(select(func.count()).select_from(DocumentChunk))
        return HealthResult(True, int((time.perf_counter() - started) * 1000), f"{count} chunks indexed")

    async def execute(self, operation: str, payload: dict[str, Any]) -> Any:
        if operation != "search_knowledge":
            raise ConnectorError(f"knowledge connector does not implement '{operation}'", kind="not_supported")
        query = str(payload.get("query") or "").strip()
        if not query:
            raise ConnectorError("query must not be empty", kind="bad_request")
        top_k = max(1, min(int(payload.get("top_k") or 5), 10))
        async with tenant_session(self.tenant_id) as session:
            chunks = await search_chunks(session, self.tenant_id, query, top_k=top_k, tenant_label=self.tenant_label)
        return [
            {
                "chunk_id": c.chunk_id,
                "document": c.document,
                "section": c.section,
                "score": c.vector_score,
                "content": c.content,
            }
            for c in chunks
        ]
