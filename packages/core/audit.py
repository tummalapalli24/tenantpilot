"""Append-only audit trail for configuration changes and human approvals.

Details are passed through ``redact`` so a credential can never land in the audit
table, even if a caller accidentally includes it.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.core.logging import redact, request_id_var
from packages.core.models import AuditLog


async def record_audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID | None,
    actor_email: str | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_email=actor_email,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=redact(details or {}),
            request_id=request_id_var.get(),
        )
    )
