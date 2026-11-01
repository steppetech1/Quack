"""Two-key session-level Postgres advisory locks (spec §4.4).

Session-level (not transactional) locks on a dedicated pooled connection, because the
projector and rebuild hold Neo4j transactions across network calls. Always released in
`finally`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.db.db import LockKeyspace

__all__ = ["LockKeyspace", "advisory_lock"]


@asynccontextmanager
async def advisory_lock(
    engine: AsyncEngine, keyspace: LockKeyspace, key_id: UUID, *, wait: bool = False
) -> AsyncIterator[bool]:
    """Yield True if `pg_(try_)advisory_lock(keyspace, hashtext(str(key_id)))` was acquired."""
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        params = {"ks": int(keyspace), "key": str(key_id)}
        if wait:
            await conn.execute(text("SELECT pg_advisory_lock(:ks, hashtext(:key))"), params)
            acquired = True
        else:
            result = await conn.execute(
                text("SELECT pg_try_advisory_lock(:ks, hashtext(:key))"), params
            )
            acquired = bool(result.scalar_one())
        try:
            yield acquired
        finally:
            if acquired:
                await conn.execute(text("SELECT pg_advisory_unlock(:ks, hashtext(:key))"), params)
