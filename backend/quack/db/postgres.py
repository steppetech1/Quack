"""Postgres pool: SQLAlchemy 2.0 async engine over asyncpg."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from quack.config import Settings

type SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(settings: Settings, *, pool_size: int = 10) -> AsyncEngine:
    return create_async_engine(
        settings.sqlalchemy_dsn, pool_size=pool_size, max_overflow=5, pool_pre_ping=True
    )


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    return async_sessionmaker(engine, expire_on_commit=False)
