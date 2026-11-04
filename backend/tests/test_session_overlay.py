"""`read_consistent_slice` (spec §8.2, Т-§2.6): reads with a watermark and the fallback when
the projector lags (D-A-228); the session overlay on `rules@3` (WP2.3, WP3.4, D-A-239).

The overlay of Phase 3 (`build_session_overlay` on `AsyncSession`) went with Phase 3 (D-A-228).
The waiting for the tail of the journal on `Db` is checked in
`graph/test_projection_runner.py::test_slice_waits_for_the_journal_of_phase_4`. The overlay of
Phase 4 reads the tail of the session and applies the handlers of `rules@3` in memory: the tests
at the end of this file."""

from __future__ import annotations

import asyncio
import copy
import json
import time
from typing import Any
from uuid import UUID, uuid4

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events, read_session_tail
from quack.graph.overlay_build import apply_overlay_events, build_session_overlay
from quack.graph.projection.runner import load_batch_inputs, project_learner
from quack.graph.slice import SliceDeps, read_consistent_slice
from tests.db.fixtures import create_learner


def _deps(redis: Redis | None, applied: dict[str, int]) -> SliceDeps:
    async def reader(learner_id: UUID) -> int:
        return applied.get(str(learner_id), 0)

    return SliceDeps(redis=redis, applied_seq_reader=reader)


async def test_slice_returns_immediately_when_caught_up(redis: Redis) -> None:
    learner = uuid4()
    applied = {str(learner): 5}
    calls: list[int] = []

    async def loader() -> dict[str, Any]:
        calls.append(1)
        return {"ok": True}

    result = await read_consistent_slice(learner, 5, loader, 2000, deps=_deps(redis, applied))
    assert result.data == {"ok": True}
    assert result.lag_degraded is False and result.stable is True
    assert result.applied_seq == 5 and len(calls) == 1


async def test_slice_waits_for_projector_applied_signal(redis: Redis) -> None:
    learner = uuid4()
    applied = {str(learner): 3}

    async def loader() -> int:
        return applied[str(learner)]

    async def projector_catches_up() -> None:
        await asyncio.sleep(0.2)
        applied[str(learner)] = 7
        await redis.publish(f"projector:applied:{learner}", json.dumps({"applied_seq": 7}))

    started = time.monotonic()
    task = asyncio.create_task(projector_catches_up())
    result = await read_consistent_slice(learner, 7, loader, 3000, deps=_deps(redis, applied))
    await task
    assert result.lag_degraded is False
    assert result.data == 7 and result.applied_seq == 7
    assert time.monotonic() - started < 2.5


async def test_slice_degrades_after_timeout(redis: Redis) -> None:
    learner = uuid4()

    async def loader() -> str:
        return "stale"

    started = time.monotonic()
    result = await read_consistent_slice(
        learner, 10, loader, 300, deps=_deps(redis, {str(learner): 2})
    )
    elapsed = time.monotonic() - started
    assert result.lag_degraded is True
    assert result.data == "stale" and result.applied_seq == 2
    assert 0.25 <= elapsed < 2.0


async def test_slice_degrades_without_redis() -> None:
    learner = uuid4()

    async def loader() -> str:
        return "stale"

    result = await read_consistent_slice(
        learner, 10, loader, 200, deps=_deps(None, {str(learner): 2})
    )
    assert result.lag_degraded is True


async def test_slice_retries_until_watermark_is_stable(redis: Redis) -> None:
    learner = uuid4()
    applied = {str(learner): 5}

    async def loader() -> str:
        # The projector commits while we read: the watermark moves once.
        if applied[str(learner)] == 5:
            applied[str(learner)] = 6
        return f"slice@{applied[str(learner)]}"

    result = await read_consistent_slice(learner, 5, loader, 2000, deps=_deps(redis, applied))
    assert result.stable is True
    assert result.data == "slice@6" and result.applied_seq == 6


# --- the session overlay on rules@3 (WP2.3, WP3.4; Т-§2.5, Т-§2.6) ---------------------------


def _ev(event_type: str, payload: dict[str, Any], key: str, session: UUID | None) -> NewEvent:
    return NewEvent(
        type=event_type, payload=payload, actor="code", idempotency_key=key, session_id=session
    )


class _Scene:
    """A learner with a domain, a topic, a skill and a task (all projected), and two sessions."""

    def __init__(self, learner: UUID) -> None:
        self.learner = learner
        self.domain, self.topic, self.skill = uuid4(), uuid4(), uuid4()
        self.task, self.family = uuid4(), uuid4()
        self.mine, self.other = uuid4(), uuid4()

    def base(self) -> list[NewEvent]:
        d = str(self.domain)
        skill = {"domain_id": d, "summary": "s", "status": "active", "origin": "session"}
        return [
            _ev("domain.created", {"domain_id": d, "name": "Algebra"}, "d", None),
            _ev(
                "topic.created",
                {"topic_id": str(self.topic), "domain_id": d, "name": "F", "origin": "session"},
                "t",
                None,
            ),
            _ev(
                "skill.created",
                skill | {"skill_id": str(self.skill), "topic_id": str(self.topic), "name": "add"},
                "s",
                None,
            ),
            _ev(
                "task.created",
                {
                    "task_id": str(self.task),
                    "family_id": str(self.family),
                    "source": "teacher",
                    "verification": "unchecked",
                    "markup": {
                        "tests": [{"skill_id": str(self.skill), "level": 3}],
                        "method_named": False,
                        "answer": {"type": "number", "value": {"latex": "2", "sympy": "2"}},
                    },
                },
                "task",
                self.mine,
            ),
        ]

    async def answer(self, db: Db, session: UUID, tag: str, outcome: str) -> list[int]:
        """An answer and its verdict in `session`; returns their `seq`s."""
        answer = {"task_id": str(self.task), "answer": {"kind": "number", "value": "2"}}
        [stored] = await append_events(
            db,
            self.learner,
            [_ev("answer.submitted", answer | {"source": "chat"}, f"a{tag}", session)],
        )
        verdict = {
            "answer_seq": stored.seq,
            "part": "main",
            "outcome": outcome,
            "method": "exact",
            "verification": "code",
        }
        [found] = await append_events(
            db, self.learner, [_ev("verdict.recorded", verdict, f"v{tag}", session)]
        )
        return [stored.seq, found.seq]


async def _scene(db: Db, neo4j: AsyncDriver, settings: Settings) -> _Scene:
    scene = _Scene(await create_learner(db))
    await append_events(db, scene.learner, scene.base())
    assert (await project_learner(db, neo4j, settings, scene.learner)).applied_seq == 4
    return scene


async def _neo4j_graph(neo4j: AsyncDriver, learner: UUID) -> tuple[dict[Any, Any], set[Any]]:
    async with neo4j.session() as session:
        nodes = await session.run(
            "MATCH (n {learnerId: $l}) RETURN labels(n)[0] AS label, n.id AS id, "
            "properties(n) AS props",
            l=str(learner),
        )
        found = {(r["label"], r["id"]): dict(r["props"]) async for r in nodes}
        rels = await session.run(
            "MATCH (a {learnerId: $l})-[r]->(b) RETURN labels(a)[0] AS fl, a.id AS a, "
            "type(r) AS t, labels(b)[0] AS tl, b.id AS b",
            l=str(learner),
        )
        links = {(r["fl"], r["a"], r["t"], r["tl"], r["b"]) async for r in rels}
    return found, links


async def test_overlay_of_fresh_events_equals_applying_them(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    """Т-§2.5, T-P1: the session events past a lagging projection give, in memory, the graph the
    projector writes when it applies them: every node and relationship of the overlay is in
    Neo4j afterwards with the same properties."""
    scene = await _scene(pg_db, neo4j, settings)
    answered = await scene.answer(pg_db, scene.mine, "1", "correct")
    before, _ = await _neo4j_graph(neo4j, scene.learner)

    overlay = await build_session_overlay(pg_db, neo4j, settings, scene.learner, scene.mine)
    assert (overlay.applied_seq, overlay.overlay_seq) == (4, answered[-1])
    assert [e.seq for e in overlay.events] == answered
    assert overlay.submitted_task_ids == [scene.task] and overlay.evaluations[scene.task].is_correct
    evidence = [k for k in overlay.graph.nodes if k[0] == "Evidence"]
    assert len(evidence) == 1 and not any(k[0] == "Evidence" for k in before)

    assert (await project_learner(pg_db, neo4j, settings, scene.learner)).applied_seq == answered[
        -1
    ]
    after, rels = await _neo4j_graph(neo4j, scene.learner)
    watermark = {"projectedSeq", "projectorVersion"}  # the projector's own, not the overlay's
    written = {
        k: {p: v for p, v in after[k].items() if p not in watermark} for k in overlay.graph.nodes
    }
    assert overlay.graph.nodes == written
    assert set(overlay.graph.rels) <= rels


async def test_overlay_is_empty_when_the_projector_is_level(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    scene = await _scene(pg_db, neo4j, settings)
    answered = await scene.answer(pg_db, scene.mine, "1", "correct")
    await project_learner(pg_db, neo4j, settings, scene.learner)

    overlay = await build_session_overlay(pg_db, neo4j, settings, scene.learner, scene.mine)
    assert (overlay.applied_seq, overlay.overlay_seq) == (answered[-1], answered[-1])
    assert overlay.events == [] and overlay.graph.nodes == {} and overlay.graph.rels == {}


async def test_overlay_writes_nothing(pg_db: Db, neo4j: AsyncDriver, settings: Settings) -> None:
    """Neither the base slice nor Neo4j nor the watermark copy in Postgres is changed."""
    scene = await _scene(pg_db, neo4j, settings)
    await scene.answer(pg_db, scene.mine, "1", "correct")
    tail = await read_session_tail(pg_db, scene.learner, scene.mine, 4)
    base, earlier = await load_batch_inputs(pg_db, neo4j, scene.learner, tail)
    frozen = copy.deepcopy(base)
    graph_before = await _neo4j_graph(neo4j, scene.learner)
    offset = "SELECT seq FROM ops.projector_offsets WHERE learner_id = %(learner_id)s"
    offset_before = await pg_db.fetch_one(offset, {"learner_id": scene.learner})

    overlay = await apply_overlay_events(scene.learner, 4, tail, base, settings, earlier=earlier)
    await build_session_overlay(pg_db, neo4j, settings, scene.learner, scene.mine)

    assert overlay.graph.nodes != {} and base == frozen
    assert await _neo4j_graph(neo4j, scene.learner) == graph_before
    assert await pg_db.fetch_one(offset, {"learner_id": scene.learner}) == offset_before


async def test_overlay_takes_only_the_events_of_its_session(
    pg_db: Db, neo4j: AsyncDriver, settings: Settings
) -> None:
    scene = await _scene(pg_db, neo4j, settings)
    mine = await scene.answer(pg_db, scene.mine, "1", "correct")
    await scene.answer(pg_db, scene.other, "2", "incorrect")

    overlay = await build_session_overlay(pg_db, neo4j, settings, scene.learner, scene.mine)
    assert [e.seq for e in overlay.events] == mine and overlay.overlay_seq == mine[-1]
    sessions = {
        str(n["sourceSessionId"]) for k, n in overlay.graph.nodes.items() if k[0] == "Evidence"
    }
    assert sessions == {str(scene.mine)}
