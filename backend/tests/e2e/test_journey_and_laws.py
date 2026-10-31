"""E2E: learner journey and core laws 1, 7, 9 (ТЗ Фазы 3 §8.7: T-E1, T-E4, T-E5, T-E6, T-E10)."""

from __future__ import annotations

import time
from uuid import UUID

from sqlalchemy import text

from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.rebuild import rebuild_learner
from tests.e2e.journey import ANSWER_WORD, first, ok, onboard, run_journey, turn
from tests.e2e.system import System
from tests.support.llm import ScriptedFailure

# Kinds whose handler reads the learner graph and must wait for `required_seq` (WP3.1).
GRAPH_READING_KINDS = (
    "turn.extract",
    "session.finish_artifacts",
    "artifact.refresh_cheatsheet",
    "graph.curate",
)


async def test_full_learner_journey(system: System) -> None:
    """T-E1: the whole path over HTTP + `settle()`."""
    j = await run_journey(system, "te1")
    learner_id = j.learner.learner_id

    async with system.session_factory() as session:
        kinds = {
            row.kind: row.n
            for row in await session.execute(
                text(
                    "SELECT kind, max(version) AS n FROM core.artifacts "
                    "WHERE learner_id = :l AND NOT is_deleted GROUP BY kind"
                ),
                {"l": learner_id},
            )
        }
        assert "digest" in kinds, kinds
        assert kinds.get("cheatsheet", 0) >= 1, kinds

        jobs = (
            await session.execute(
                text(
                    "SELECT id, kind, status, required_seq FROM ops.jobs "
                    "WHERE learner_id = :l AND kind = ANY(:kinds)"
                ),
                {"l": learner_id, "kinds": list(GRAPH_READING_KINDS)},
            )
        ).all()
        assert {j.kind for j in jobs} == set(GRAPH_READING_KINDS), [j.kind for j in jobs]
        assert all(j.status == "done" for j in jobs), [(j.kind, j.status) for j in jobs]

        # Every graph-reading job started after the projector applied its `required_seq`.
        starts = {s.job_id: s for s in system.job_starts}
        for job in jobs:
            assert job.required_seq is not None, f"{job.kind} has no required_seq"
            assert starts[job.id].applied_seq >= job.required_seq, (
                f"{job.kind} started at applied_seq {starts[job.id].applied_seq} "
                f"< required_seq {job.required_seq}"
            )

        # The dispatcher placed the jobs and caught up with the learner's log (WP11).
        last_seq = await session.scalar(
            text("SELECT last_seq FROM core.learner_seq WHERE learner_id = :l"), {"l": learner_id}
        )
        dispatched = await session.scalar(
            text("SELECT last_dispatched_seq FROM ops.dispatcher_offsets WHERE learner_id = :l"),
            {"l": learner_id},
        )
        assert dispatched == last_seq

        target_skills = await session.scalar(
            text("SELECT target_skills FROM core.task_bank WHERE task_id = :t"),
            {"t": j.practice_task_id},
        )
    solved_skills = {str(s["skill_id"]) for s in target_skills or []}
    assert solved_skills, "practice task has no target skills"

    api, h = system.client, j.learner.headers
    ok(await api.get(f"/v1/domains/{j.domain_id}/graph", headers=h))
    detail = ok(await api.get(f"/v1/domains/{j.domain_id}/topics/{j.topic_id}", headers=h))
    mastery = {s["skill_id"]: s["p_mastery"] for s in detail["skills"]}
    assert any(mastery.get(s, 0.0) > 0 for s in solved_skills), mastery

    export = ok(
        await api.get(f"/v1/domains/{j.domain_id}/export", params={"format": "json"}, headers=h)
    )
    assert export["topics"], export
    assert export["digests"], export
    assert any(t["cheatsheet"] for t in export["topics"]), export["topics"]

    found = ok(await api.get("/v1/artifacts/search", params={"q": ANSWER_WORD}, headers=h))
    assert found["event_hits"], found


async def _graph_snapshot(system: System, learner_id: UUID) -> object:
    return (await Neo4jGraphStore(system.neo4j, str(learner_id)).dump()).snapshot()


async def test_rebuild_equals_incremental(system: System) -> None:
    """T-E4 (law 1): after T-E1 a rebuild from the log gives the same graph."""
    j = await run_journey(system, "te4")
    learner_id = j.learner.learner_id
    before = await _graph_snapshot(system, learner_id)
    await system.pause_background()
    async with system.neo4j.session() as session:
        await session.run("MATCH (n {learnerId: $l}) DETACH DELETE n", l=str(learner_id))
    outcome = await rebuild_learner(
        system.settings,
        system.engine,
        system.session_factory,
        system.neo4j,
        system.redis,
        learner_id,
        respect_presence=False,
    )
    assert outcome.status == "rebuilt", outcome
    assert await _graph_snapshot(system, learner_id) == before


async def test_failed_background_job_commits_nothing(system: System) -> None:
    """T-E5 (law 7): `turn.extract` fails on invalid JSON twice and commits nothing."""
    learner = await system.register()
    domain_id, topic_id = await onboard(system, learner, "te5")
    session = ok(
        await system.client.post(
            "/v1/sessions",
            json={
                "domain_id": str(domain_id),
                "topic_id": str(topic_id),
                "idempotency_key": "te5:s",
            },
            headers=learner.headers,
        ),
        201,
    )
    session_id = UUID(session["session_id"])
    # The gateway retries a schema failure once (Т-§11.1): two bad answers fail the attempt.
    system.llm.fail(ScriptedFailure("invalid_json", schema_name="ExtractorOutputV2", times=2))
    events = await turn(system, learner, session_id, "I think it is 2x", "te5:t1")
    started = first(events, "turn_started")
    assert started is not None
    turn_id = started["turn_id"]
    await system.settle()

    async with system.session_factory() as s:
        extracted = await s.scalar(
            text(
                "SELECT count(*) FROM core.events WHERE learner_id = :l "
                "AND type = 'turn.extracted' AND payload->>'turn_id' = :t"
            ),
            {"l": learner.learner_id, "t": turn_id},
        )
        turn_events = await s.scalar(
            text(
                "SELECT count(*) FROM core.events WHERE learner_id = :l "
                "AND type IN ('turn.student_replied', 'turn.teacher_replied') "
                "AND payload->>'turn_id' = :t"
            ),
            {"l": learner.learner_id, "t": turn_id},
        )
        job = (
            await s.execute(
                text(
                    "SELECT status, attempt, run_after > now() AS later FROM ops.jobs "
                    "WHERE learner_id = :l AND kind = 'turn.extract'"
                ),
                {"l": learner.learner_id},
            )
        ).one()
    assert extracted == 0
    assert turn_events == 2
    assert (job.status, job.attempt, job.later) == ("queued", 1, True)

    async with system.session_factory() as s, s.begin():
        await s.execute(
            text("UPDATE ops.jobs SET run_after = now() WHERE learner_id = :l"),
            {"l": learner.learner_id},
        )
    await system.settle()
    async with system.session_factory() as s:
        extracted = await s.scalar(
            text(
                "SELECT count(*) FROM core.events WHERE learner_id = :l "
                "AND type = 'turn.extracted' AND payload->>'turn_id' = :t"
            ),
            {"l": learner.learner_id, "t": turn_id},
        )
    assert extracted == 1


async def test_hot_path_never_waits_for_background(system: System) -> None:
    """T-E6 (law 9): with the background paused turns finish in time, on a lagging graph."""
    learner = await system.register()
    domain_id, topic_id = await onboard(system, learner, "te6")
    session = ok(
        await system.client.post(
            "/v1/sessions",
            json={
                "domain_id": str(domain_id),
                "topic_id": str(topic_id),
                "idempotency_key": "te6:s",
            },
            headers=learner.headers,
        ),
        201,
    )
    session_id = UUID(session["session_id"])
    await system.pause_background()
    budget_s = system.settings.projector_lag_timeout_ms / 1000 + 2.0

    assigned: list[str] = []
    modes: list[str | None] = []
    for n in range(3):
        t0 = time.monotonic()
        events = await turn(system, learner, session_id, f"step {n}", f"te6:t{n}")
        assert time.monotonic() - t0 <= budget_s, f"turn {n} waited for the background"
        completed = first(events, "turn_completed")
        assert completed is not None
        modes.append(completed["degraded_mode"])
        task = first(events, "task_assigned")
        if task is not None:
            assigned.append(task["task_id"])
    assert modes[1] == "projector_lag" and modes[2] == "projector_lag", modes
    assert len(assigned) == len(set(assigned)), assigned

    system.resume_background()
    await system.settle()
    async with system.session_factory() as s:
        lag = (
            await s.execute(
                text(
                    "SELECT s.last_seq, p.applied_seq FROM core.learner_seq s "
                    "JOIN ops.projector_offsets p USING (learner_id) WHERE s.learner_id = :l"
                ),
                {"l": learner.learner_id},
            )
        ).one()
    assert lag.applied_seq == lag.last_seq


async def test_account_deletion_end_to_end(system: System) -> None:
    """T-E10: after T-E1 the account is purged by the scheduler (as in T-Q11)."""
    j = await run_journey(system, "te10")
    deleted = ok(await system.client.delete("/v1/learner", headers=j.learner.headers))
    assert deleted["status"] == "pending_purge"

    # `purge_deleted_interval_s` arrives with WP3.3 (stream A, §6.2).
    system.clock.advance(system.settings.model_dump()["purge_deleted_interval_s"])
    await system.settle()

    learner_id = j.learner.learner_id
    async with system.session_factory() as s:
        events = await s.scalar(
            text("SELECT count(*) FROM core.events WHERE learner_id = :l"), {"l": learner_id}
        )
        status = await s.scalar(
            text("SELECT status FROM ops.deletion_tombstones WHERE learner_id = :l"),
            {"l": learner_id},
        )
    assert events == 0
    assert status == "purged"
    assert await _graph_snapshot(system, learner_id) == await _empty_snapshot(system)
    if system.redis is not None:
        assert await system.redis.get(f"presence:learner:{learner_id}") is None

    login = await system.client.post(
        "/v1/auth/login", json={"email": j.learner.email, "password": j.learner.password}
    )
    assert login.status_code == 401, login.text


async def _empty_snapshot(system: System) -> object:
    return (
        await Neo4jGraphStore(system.neo4j, "00000000-0000-0000-0000-000000000000").dump()
    ).snapshot()
