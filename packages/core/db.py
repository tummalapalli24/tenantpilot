"""Database engine + sessions with tenant isolation enforced by Postgres Row-Level Security.

Two kinds of sessions exist:

* ``platform_session()`` - for control-plane tables (tenants, users, memberships) that
  are not tenant-scoped. RLS-protected tables return *zero rows* here, because no
  tenant is set (fail closed).
* ``tenant_session(tenant_id)`` - sets ``app.current_tenant`` *transaction-locally*
  (``set_config(..., true)``). Every RLS policy compares ``tenant_id`` against that
  setting, so even a query that forgets ``WHERE tenant_id = ...`` cannot read another
  tenant's rows. Transaction-local means the setting can never leak to the next user of
  a pooled connection.

Keep transactions short: never hold one open across an LLM or HTTP call.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from packages.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        _instrument(_engine)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def _instrument(engine: AsyncEngine) -> None:
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    except Exception:  # pragma: no cover - tracing is optional, never fatal
        logging.getLogger(__name__).warning("SQLAlchemy instrumentation unavailable", exc_info=True)


def session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine, _session_factory = None, None


@asynccontextmanager
async def platform_session() -> AsyncIterator[AsyncSession]:
    """Transaction for control-plane tables. Commits on success, rolls back on error."""
    async with session_factory()() as session, session.begin():
        yield session


@asynccontextmanager
async def tenant_session(tenant_id: UUID | str) -> AsyncIterator[AsyncSession]:
    """Transaction in which RLS only exposes rows belonging to ``tenant_id``."""
    tid = str(UUID(str(tenant_id)))  # validates format; never interpolate raw input into SQL
    async with session_factory()() as session, session.begin():
        await session.execute(text("SELECT set_config('app.current_tenant', :tid, true)"), {"tid": tid})
        yield session
