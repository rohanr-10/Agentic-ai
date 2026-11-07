"""Async engine/session management.

``Database`` owns the engine and session factory for one process.  The API
application stores it on ``app.state.database``; the worker process
constructs its own.  SQLite connections are tuned for concurrent access
(WAL, busy timeout, enforced foreign keys).
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from typing import Any

import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger("app.db")


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            echo=settings.db_echo,
            pool_pre_ping=True,
            **self._engine_kwargs(settings),
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        if self._is_sqlite:
            _install_sqlite_pragmas(self.engine)
        logger.info(
            "database_engine_created",
            dialect=self.engine.dialect.name,
        )

    @property
    def _is_sqlite(self) -> bool:
        return self.engine.dialect.name == "sqlite"

    @staticmethod
    def _engine_kwargs(settings: Settings) -> dict[str, Any]:
        if settings.database_url.startswith("sqlite"):
            # aiosqlite: single connection per session; the busy timeout
            # below governs lock contention between sessions.
            return {"connect_args": {"timeout": 30}}
        return {
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
            "pool_timeout": settings.db_pool_timeout_s,
        }

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Session context: commit/rollback is the caller's responsibility.

        On exit the session is closed, which implicitly rolls back any
        still-open transaction — a request or job that forgot to commit
        never leaks partial writes.  (No explicit rollback: that would
        expire ORM instances that callers may still read.)
        """
        async with self.session_factory() as session:
            yield session

    @contextlib.asynccontextmanager
    async def begin(self) -> AsyncIterator[AsyncSession]:
        """Session context with an explicit transaction (commit on success)."""
        async with self.session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def ping(self) -> bool:
        try:
            async with self.engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))
            return True
        except Exception:
            return False

    async def dispose(self) -> None:
        await self.engine.dispose()
        logger.info("database_engine_disposed")


def _install_sqlite_pragmas(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=30000")
        finally:
            cursor.close()
