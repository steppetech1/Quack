"""WP4.1: READ sessions of Neo4j (ТЗ Фазы 3 §8.2, T-A9b)."""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from neo4j import READ_ACCESS, AsyncDriver
from neo4j.exceptions import Neo4jError

from quack.db.neo4j import read_session
from quack.graph.projection.store import (
    load_fold_inputs,
    load_profile_evidence,
    load_slice,
    load_topic_inputs,
    read_watermark,
    stale_learners,
)
from quack.graph.projection.view import Need


async def test_read_session_reads(neo4j: AsyncDriver) -> None:
    async with read_session(neo4j) as session:
        result = await session.run("RETURN 1 AS one")
        record = await result.single()
    assert record is not None and record["one"] == 1


async def test_read_session_rejects_writes(neo4j: AsyncDriver) -> None:
    """T-A9b [ПРОВЕРИТЬ Д-§14.2]: `CREATE` through `read_session` fails in the driver and
    the node is not created."""
    probe = str(uuid4())
    with pytest.raises(Neo4jError):
        async with read_session(neo4j) as session:
            result = await session.run("CREATE (:Probe {id: $id})", {"id": probe})
            await result.consume()
    async with neo4j.session() as session:
        result = await session.run("MATCH (p:Probe {id: $id}) RETURN count(p) AS n", {"id": probe})
        record = await result.single()
    assert record is not None and record["n"] == 0


class _AccessModes:
    """A driver that records the access mode of every session it opens (D-A-229)."""

    def __init__(self, driver: AsyncDriver) -> None:
        self._driver = driver
        self.modes: list[object] = []

    def session(self, **config: Any) -> Any:
        self.modes.append(config.get("default_access_mode"))
        return self._driver.session(**config)


async def test_batch_reads_of_the_projector_open_read_sessions(neo4j: AsyncDriver) -> None:
    """D-C-016, D-A-229: every read of the batch of `rules@3` (the slice, the folds, the topics,
    the profile, the watermark, the stale learners) opens a READ session, so a write cannot go
    through them."""
    spy = _AccessModes(neo4j)
    driver, learner = cast(AsyncDriver, spy), str(uuid4())
    assert await read_watermark(driver, learner) is None
    assert (await load_slice(driver, learner, Need())).nodes == []
    assert (await load_fold_inputs(driver, learner, set(), set())).nodes == []
    await load_topic_inputs(driver, learner, set(), set(), set())
    assert (await load_profile_evidence(driver, learner)).nodes == []
    assert await stale_learners(driver, "rules@3") == []
    assert len(spy.modes) == 6
    assert set(spy.modes) == {READ_ACCESS}
