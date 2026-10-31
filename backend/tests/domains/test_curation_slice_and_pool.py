"""`graph.curate`: the domain graph is read through `read_consistent_slice` in READ mode, and
`handle_graph_curate` reuses one Neo4j driver, Redis client and `LLMGateway` across jobs
(РТЗ D-7.2, D-7.3).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import neo4j
import pytest

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.domains import curation
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __aiter__(self) -> _Result:
        self._it = iter(self._rows)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration from None

    async def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, driver: _Driver, mode: str | None) -> None:
        self._driver = driver
        self._mode = mode

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def run(self, query: str, params: dict[str, Any]) -> _Result:
        self._driver.queries.append((query, self._mode))
        if "RETURN d.name" in query:
            return _Result([{"name": "Algebra"}])
        return _Result([])


class _Driver:
    def __init__(self) -> None:
        self.queries: list[tuple[str, str | None]] = []

    def session(self, **kwargs: Any) -> _Session:
        return _Session(self, kwargs.get("default_access_mode"))

    async def close(self) -> None:
        return None


class _Redis:
    def __init__(self) -> None:
        self.closed = False

    async def exists(self, *keys: str) -> int:
        return 0

    async def aclose(self) -> None:
        self.closed = True


def _job(learner_id: UUID, domain_id: UUID) -> ClaimedJob:
    return ClaimedJob(
        id=uuid4(),
        kind="graph.curate",
        learner_id=learner_id,
        lock_key=f"curate:{domain_id}",
        coalesce_key=None,
        priority=0,
        payload={"domain_id": str(domain_id)},
        attempt=1,
        max_attempts=3,
    )


async def test_handle_graph_curate_creates_driver_redis_and_gateway_once(
    db: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, redis = _Driver(), _Redis()
    calls = {"driver": 0, "redis": 0, "gateway": 0}

    def _create_driver(_settings: Settings) -> _Driver:
        calls["driver"] += 1
        return driver

    def _create_redis(_settings: Settings) -> _Redis:
        calls["redis"] += 1
        return redis

    class _Gateway:
        def __init__(self, **_kwargs: Any) -> None:
            calls["gateway"] += 1

    monkeypatch.setattr(curation, "_RESOURCES", {})
    monkeypatch.setattr(curation, "create_driver", _create_driver)
    monkeypatch.setattr(curation, "create_redis", _create_redis)
    monkeypatch.setattr(curation, "LLMGateway", _Gateway)

    ctx = JobContext(settings=settings.model_copy(), session_factory=db)
    for _ in range(2):
        await curation.handle_graph_curate(_job(uuid4(), uuid4()), ctx)

    assert calls == {"driver": 1, "redis": 1, "gateway": 1}
    assert not redis.closed
    assert driver.queries, "the curation job did not read the graph"

    other = JobContext(settings=settings.model_copy(), session_factory=db)
    await curation.handle_graph_curate(_job(uuid4(), uuid4()), other)
    assert calls == {"driver": 2, "redis": 2, "gateway": 2}


async def test_curation_reads_graph_through_consistent_slice_in_read_mode(
    db: SessionFactory, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, redis = _Driver(), _Redis()
    slices: list[dict[str, Any]] = []
    real_slice = curation.read_consistent_slice

    async def _spy(*args: Any, **kwargs: Any) -> Any:
        slices.append({"args": args, "kwargs": kwargs, "queries_before": len(driver.queries)})
        return await real_slice(*args, **kwargs)

    monkeypatch.setattr(curation, "read_consistent_slice", _spy)

    learner_id, domain_id = uuid4(), uuid4()
    ctx = JobContext(settings=settings, session_factory=db)
    await curation.curate_domain_graph(
        _job(learner_id, domain_id),
        ctx,
        driver=driver,  # type: ignore[arg-type]
        redis=redis,  # type: ignore[arg-type]
        llm=None,
    )

    assert len(slices) == 1
    call = slices[0]
    assert call["args"][0] == learner_id
    assert call["args"][1] is None  # min_seq resolved from the learner's last event seq
    assert call["args"][3] == settings.projector_lag_timeout_ms
    assert call["queries_before"] == 0, "graph queries ran before the slice read"

    domain_queries = [(q, mode) for q, mode in driver.queries if "(d:Domain" in q]
    assert domain_queries
    assert all(mode == neo4j.READ_ACCESS for _, mode in domain_queries)
    graph_queries = [mode for q, mode in driver.queries if "MATCH (t:Topic {" in q]
    assert graph_queries == [neo4j.READ_ACCESS]
