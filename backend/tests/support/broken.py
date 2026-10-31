"""Broken dependencies for degradation tests (ТЗ Фазы 3 §8.1)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, NoReturn

from neo4j.exceptions import ServiceUnavailable
from redis.exceptions import ConnectionError as RedisConnectionError


class BrokenRedis:
    """Redis client stand-in: every command raises `redis.exceptions.ConnectionError`."""

    def __getattr__(self, name: str) -> Callable[..., Awaitable[NoReturn]]:
        async def fail(*args: Any, **kwargs: Any) -> NoReturn:
            raise RedisConnectionError(f"broken redis: {name}")

        return fail

    def pubsub(self, *args: Any, **kwargs: Any) -> BrokenRedis:
        return self

    def pipeline(self, *args: Any, **kwargs: Any) -> BrokenRedis:
        return self


class BrokenNeo4jDriver:
    """Neo4j driver stand-in: opening a session or checking connectivity raises
    `neo4j.exceptions.ServiceUnavailable`."""

    def session(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ServiceUnavailable("broken neo4j")

    async def verify_connectivity(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise ServiceUnavailable("broken neo4j")

    async def close(self) -> None:
        return None
