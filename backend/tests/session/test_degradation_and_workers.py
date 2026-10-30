"""Turn lock and its Redis degradation (ТЗ §2.3.1, Т-§17.3).

`acquire_turn_lock` takes `SET lock:turn:{session_id} <token> NX EX 60` in Redis; when
Redis is `None` or unreachable it falls back to the Postgres session advisory lock in
keyspace 2 (`LockKeyspace.SESSION_TURN`). A busy lock raises `ApiError("TURN_LOCKED")`
with status 409, and the lock is released in `finally`.
"""

from __future__ import annotations

import asyncio
import inspect
import socket
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import UUID, uuid4

import pytest
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.errors import ApiError
from quack.db.locks import LockKeyspace
from quack.db.redis import turn_lock_key
from quack.session.locks import acquire_turn_lock

pytest_plugins = ["tests.session.fixtures"]


# --- helpers -----------------------------------------------------------------------------


def turn_lock(
    redis: Redis | None, engine: AsyncEngine, session_id: UUID
) -> AbstractAsyncContextManager[Any]:
    """Call `acquire_turn_lock(redis, engine, session_id)`.

    A `request_id` (used as the Redis token by ТЗ §2.3.1) is passed only if the
    implementation's signature asks for one.
    """
    extra: dict[str, Any] = {}
    if "request_id" in inspect.signature(acquire_turn_lock).parameters:
        extra["request_id"] = f"req-{uuid4()}"
    return acquire_turn_lock(redis, engine, session_id, **extra)


def dead_port() -> int:
    """A local TCP port nobody listens on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def dead_redis() -> AsyncIterator[Redis]:
    """A Redis client pointed at a dead port: every command raises a connection error."""
    client = Redis(
        host="127.0.0.1",
        port=dead_port(),
        decode_responses=True,
        socket_connect_timeout=0.5,
        socket_timeout=0.5,
        retry=Retry(NoBackoff(), 0),
    )
    yield client
    await client.aclose()


async def try_pg_lock(engine: AsyncEngine, keyspace: int, session_id: UUID) -> bool:
    """Probe `pg_try_advisory_lock(keyspace, hashtext(id))` on a fresh connection.

    The probe releases the lock immediately if it got it.
    """
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        params = {"ks": keyspace, "key": str(session_id)}
        got = (
            await conn.execute(text("SELECT pg_try_advisory_lock(:ks, hashtext(:key))"), params)
        ).scalar_one()
        if got:
            await conn.execute(text("SELECT pg_advisory_unlock(:ks, hashtext(:key))"), params)
        return bool(got)


@asynccontextmanager
async def external_pg_lock(
    engine: AsyncEngine, keyspace: int, session_id: UUID
) -> AsyncIterator[None]:
    """Hold `pg_advisory_lock(keyspace, hashtext(id))` from another connection."""
    async with engine.connect() as conn:
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        params = {"ks": keyspace, "key": str(session_id)}
        got = (
            await conn.execute(text("SELECT pg_try_advisory_lock(:ks, hashtext(:key))"), params)
        ).scalar_one()
        assert got, "test setup: the advisory lock must be free"
        try:
            yield
        finally:
            await conn.execute(text("SELECT pg_advisory_unlock(:ks, hashtext(:key))"), params)


def assert_turn_locked(exc: BaseException) -> None:
    assert isinstance(exc, ApiError), f"expected ApiError, got {exc!r}"
    assert exc.code == "TURN_LOCKED"
    assert exc.status_code == 409


async def second_attempt(redis: Redis | None, engine: AsyncEngine, session_id: UUID) -> bool:
    """Try to take the lock; return True if the body ran, re-raise ApiError otherwise."""
    entered = False
    async with turn_lock(redis, engine, session_id):
        entered = True
    return entered


async def run_concurrent_pair(
    redis: Redis | None, engine: AsyncEngine, session_id: UUID
) -> tuple[BaseException | None, bool]:
    """Holder task keeps the lock while a concurrent task tries to take it.

    Returns (exception of the contender or None, whether the contender's body ran).
    """
    holding = asyncio.Event()
    release = asyncio.Event()
    contender_entered = False

    async def holder() -> None:
        async with turn_lock(redis, engine, session_id):
            holding.set()
            await release.wait()

    async def contender() -> None:
        nonlocal contender_entered
        await holding.wait()
        async with turn_lock(redis, engine, session_id):
            contender_entered = True

    holder_task = asyncio.create_task(holder())
    try:
        await asyncio.wait_for(holding.wait(), timeout=10)
        contender_exc: BaseException | None = None
        try:
            await asyncio.wait_for(contender(), timeout=10)
        except ApiError as exc:
            contender_exc = exc
    finally:
        release.set()
        await asyncio.wait_for(holder_task, timeout=10)
    return contender_exc, contender_entered


DEGRADED = ["none", "dead_port"]


@pytest.fixture(params=DEGRADED)
def degraded_redis(request: pytest.FixtureRequest, dead_redis: Redis) -> Redis | None:
    """`redis=None` and a Redis client pointed at a dead port (ТЗ "Redis degradation")."""
    return None if request.param == "none" else dead_redis


# --- Redis degradation: Postgres advisory lock keyspace 2 ---------------------------------


async def test_degraded_concurrent_second_turn_gets_turn_locked(
    pg_engine: AsyncEngine, degraded_redis: Redis | None
) -> None:
    session_id = uuid4()
    exc, entered = await run_concurrent_pair(degraded_redis, pg_engine, session_id)
    assert exc is not None, "the concurrent second acquire must fail with TURN_LOCKED"
    assert_turn_locked(exc)
    assert entered is False


async def test_degraded_lock_is_free_again_after_first_exits(
    pg_engine: AsyncEngine, degraded_redis: Redis | None
) -> None:
    session_id = uuid4()
    async with turn_lock(degraded_redis, pg_engine, session_id):
        with pytest.raises(ApiError) as info:
            await second_attempt(degraded_redis, pg_engine, session_id)
        assert_turn_locked(info.value)
    assert await second_attempt(degraded_redis, pg_engine, session_id) is True
    # and once more, to be sure the second holder released it as well
    assert await second_attempt(degraded_redis, pg_engine, session_id) is True
    assert await try_pg_lock(pg_engine, 2, session_id) is True


async def test_degraded_lock_is_held_in_postgres_keyspace_2(
    pg_engine: AsyncEngine, degraded_redis: Redis | None
) -> None:
    session_id = uuid4()
    assert int(LockKeyspace.SESSION_TURN) == 2
    assert await try_pg_lock(pg_engine, 2, session_id) is True
    async with turn_lock(degraded_redis, pg_engine, session_id):
        assert await try_pg_lock(pg_engine, 2, session_id) is False
        # the turn lock uses keyspace 2 only, not the projector keyspace 1
        assert await try_pg_lock(pg_engine, 1, session_id) is True
    assert await try_pg_lock(pg_engine, 2, session_id) is True


async def test_degraded_external_keyspace_2_holder_blocks_the_turn(
    pg_engine: AsyncEngine, degraded_redis: Redis | None
) -> None:
    session_id = uuid4()
    async with external_pg_lock(pg_engine, 2, session_id):
        with pytest.raises(ApiError) as info:
            await second_attempt(degraded_redis, pg_engine, session_id)
        assert_turn_locked(info.value)
    assert await second_attempt(degraded_redis, pg_engine, session_id) is True


async def test_degraded_keyspace_1_holder_does_not_block_the_turn(
    pg_engine: AsyncEngine, degraded_redis: Redis | None
) -> None:
    session_id = uuid4()
    async with external_pg_lock(pg_engine, 1, session_id):
        assert await second_attempt(degraded_redis, pg_engine, session_id) is True


async def test_degraded_lock_is_per_session(
    pg_engine: AsyncEngine, degraded_redis: Redis | None
) -> None:
    first, other = uuid4(), uuid4()
    async with turn_lock(degraded_redis, pg_engine, first):
        assert await second_attempt(degraded_redis, pg_engine, other) is True
        with pytest.raises(ApiError) as info:
            await second_attempt(degraded_redis, pg_engine, first)
        assert_turn_locked(info.value)


async def test_degraded_lock_is_released_when_the_body_raises(
    pg_engine: AsyncEngine, degraded_redis: Redis | None
) -> None:
    session_id = uuid4()

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with turn_lock(degraded_redis, pg_engine, session_id):
            raise Boom
    assert await try_pg_lock(pg_engine, 2, session_id) is True
    assert await second_attempt(degraded_redis, pg_engine, session_id) is True


# --- healthy Redis: SET NX EX 60 with token-checked release -------------------------------


async def test_redis_lock_sets_key_with_ttl_and_blocks_second_turn(
    pg_engine: AsyncEngine, redis: Redis
) -> None:
    session_id = uuid4()
    key = turn_lock_key(session_id)
    async with turn_lock(redis, pg_engine, session_id):
        assert await redis.exists(key) == 1
        ttl = await redis.ttl(key)
        assert 0 < ttl <= 60
        exc, entered = await run_concurrent_pair_inner(redis, pg_engine, session_id)
        assert_turn_locked(exc)
        assert entered is False
    assert await redis.exists(key) == 0
    assert await second_attempt(redis, pg_engine, session_id) is True
    assert await redis.exists(key) == 0


async def run_concurrent_pair_inner(
    redis: Redis | None, engine: AsyncEngine, session_id: UUID
) -> tuple[BaseException, bool]:
    """Run a contender while the caller already holds the lock."""
    entered = False

    async def contender() -> None:
        nonlocal entered
        async with turn_lock(redis, engine, session_id):
            entered = True

    with pytest.raises(ApiError) as info:
        await asyncio.wait_for(contender(), timeout=10)
    return info.value, entered


async def test_redis_key_held_by_someone_else_gives_turn_locked(
    pg_engine: AsyncEngine, redis: Redis
) -> None:
    session_id = uuid4()
    key = turn_lock_key(session_id)
    await redis.set(key, "someone-else", ex=60)
    with pytest.raises(ApiError) as info:
        await second_attempt(redis, pg_engine, session_id)
    assert_turn_locked(info.value)
    # a failed acquire must not delete the other holder's key
    assert await redis.get(key) == "someone-else"


async def test_redis_release_keeps_a_key_that_is_no_longer_ours(
    pg_engine: AsyncEngine, redis: Redis
) -> None:
    session_id = uuid4()
    key = turn_lock_key(session_id)
    async with turn_lock(redis, pg_engine, session_id):
        # our lock expired and another turn took it
        await redis.set(key, "next-turn-token", ex=60)
    assert await redis.get(key) == "next-turn-token"


async def test_redis_tokens_differ_between_holders(pg_engine: AsyncEngine, redis: Redis) -> None:
    session_id = uuid4()
    key = turn_lock_key(session_id)
    async with turn_lock(redis, pg_engine, session_id):
        first_token = await redis.get(key)
    async with turn_lock(redis, pg_engine, session_id):
        second_token = await redis.get(key)
    assert first_token is not None
    assert second_token is not None
    assert first_token != second_token


async def test_redis_lock_released_when_the_body_raises(
    pg_engine: AsyncEngine, redis: Redis
) -> None:
    session_id = uuid4()
    key = turn_lock_key(session_id)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        async with turn_lock(redis, pg_engine, session_id):
            raise Boom
    assert await redis.exists(key) == 0
