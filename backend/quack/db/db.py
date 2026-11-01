"""`Db`: the Postgres access interface of Phase 4 (ТЗ Ф4 WP1.2, seam S2; Т-§1.2).

psycopg 3 over `psycopg_pool.AsyncConnectionPool`. SQL lives only in repository modules:
parameters are `%(name)s` only, dynamic SQL is built with `psycopg.sql`, rows become Pydantic
models through `class_row`. `transaction()` nests: an inner call joins the outer transaction
of the same task, so `append_events` can run inside the caller's transaction (Т-§2.3).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from enum import IntEnum
from typing import Any, overload
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.abc import QueryNoTemplate as Query
from psycopg.rows import class_row, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from quack.config import Settings

type Params = Mapping[str, Any]


class LockKeyspace(IntEnum):
    """First key of the two-key advisory locks (Т-§2.4, Т-§2.9)."""

    LEARNER_PROJECTION = 1  # projector, rebuild, deletion
    SESSION_TURN = 2  # turn lock fallback when Redis is down (Т-§20.7)


class Db:
    """One per process (ТЗ Ф4 §4.2); created by `open_db` and closed by `close`."""

    def __init__(self, pool: AsyncConnectionPool[Any]) -> None:
        self._pool = pool
        self._current: ContextVar[AsyncConnection[Any] | None] = ContextVar(
            f"quack_db_conn_{id(self)}", default=None
        )
        self._on_commit: ContextVar[list[Callable[[], None]] | None] = ContextVar(
            f"quack_db_on_commit_{id(self)}", default=None
        )

    @property
    def pool(self) -> AsyncConnectionPool[Any]:
        return self._pool

    async def close(self) -> None:
        await self._pool.close()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection[Any]]:
        """Run the block in one transaction; a nested call reuses the outer one."""
        current = self._current.get()
        if current is not None:
            yield current
            return
        callbacks: list[Callable[[], None]] = []
        async with self._pool.connection() as conn, conn.transaction():
            token = self._current.set(conn)
            cb_token = self._on_commit.set(callbacks)
            try:
                yield conn
            finally:
                self._on_commit.reset(cb_token)
                self._current.reset(token)
        # Reached only when the outermost transaction committed.
        for callback in callbacks:
            callback()

    def on_commit(self, callback: Callable[[], None]) -> None:
        """Run `callback` after the outermost transaction of this task commits (not on rollback).

        Callbacks must not block: they schedule work (for example, a Redis signal) and return.
        """
        callbacks = self._on_commit.get()
        if callbacks is None:
            raise RuntimeError("on_commit outside Db.transaction()")
        callbacks.append(callback)

    @overload
    async def fetch_one[M: BaseModel](
        self, query: Query, params: Params | None = None, *, row: type[M]
    ) -> M | None: ...

    @overload
    async def fetch_one(
        self, query: Query, params: Params | None = None, *, row: None = None
    ) -> dict[str, Any] | None: ...

    async def fetch_one(
        self, query: Query, params: Params | None = None, *, row: type[BaseModel] | None = None
    ) -> Any:
        async with self.transaction() as conn:
            factory = class_row(row) if row is not None else dict_row
            async with conn.cursor(row_factory=factory) as cur:
                await cur.execute(query, params)
                return await cur.fetchone()

    @overload
    async def fetch_all[M: BaseModel](
        self, query: Query, params: Params | None = None, *, row: type[M]
    ) -> list[M]: ...

    @overload
    async def fetch_all(
        self, query: Query, params: Params | None = None, *, row: None = None
    ) -> list[dict[str, Any]]: ...

    async def fetch_all(
        self, query: Query, params: Params | None = None, *, row: type[BaseModel] | None = None
    ) -> list[Any]:
        async with self.transaction() as conn:
            factory = class_row(row) if row is not None else dict_row
            async with conn.cursor(row_factory=factory) as cur:
                await cur.execute(query, params)
                return await cur.fetchall()

    async def execute(self, query: Query, params: Params | None = None) -> int:
        """Run a statement; returns the number of affected rows."""
        async with self.transaction() as conn, conn.cursor() as cur:
            await cur.execute(query, params)
            return cur.rowcount

    async def executemany(self, query: Query, params_seq: Iterable[Params]) -> None:
        async with self.transaction() as conn, conn.cursor() as cur:
            await cur.executemany(query, params_seq)

    @asynccontextmanager
    async def advisory_lock(
        self, keyspace: LockKeyspace, key_id: UUID, *, wait: bool = False
    ) -> AsyncIterator[bool]:
        """Session-level two-key advisory lock on a dedicated pooled connection (Т-§2.4).

        Not transactional: the projector and rebuild hold Neo4j transactions across network
        calls. Yields True if `pg_(try_)advisory_lock(keyspace, hashtext(key))` was taken;
        always released in `finally`.
        """
        params = {"ks": int(keyspace), "key": str(key_id)}
        async with self._pool.connection() as conn:
            await conn.set_autocommit(True)
            try:
                if wait:
                    await conn.execute("SELECT pg_advisory_lock(%(ks)s, hashtext(%(key)s))", params)
                    acquired = True
                else:
                    cur = await conn.execute(
                        "SELECT pg_try_advisory_lock(%(ks)s, hashtext(%(key)s))", params
                    )
                    found = await cur.fetchone()
                    acquired = bool(found and found[0])
                try:
                    yield acquired
                finally:
                    if acquired:
                        await conn.execute(
                            "SELECT pg_advisory_unlock(%(ks)s, hashtext(%(key)s))", params
                        )
            finally:
                await conn.set_autocommit(False)


async def open_db(settings: Settings) -> Db:
    """Create and open the pool (once per process, `quack.runtime`)."""
    pool = AsyncConnectionPool(
        settings.postgres_dsn,
        min_size=settings.pg_pool_min_size,
        max_size=settings.pg_pool_max_size,
        open=False,
    )
    await pool.open(wait=True)
    return Db(pool)
