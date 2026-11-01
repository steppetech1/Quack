"""Operations (ТЗ Фазы 3 §8.6): T-O1…T-O4, T-O6."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from neo4j import AsyncDriver
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack import cli
from quack.api.app import create_app
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import llm_circuit_key, llm_pressure_key
from quack.events import append_events
from quack.events.catalog import EVENT_TYPE_VERSIONS
from quack.events.models import NewEvent
from quack.graph.worker import ProjectorWorker
from quack.jobs import kinds
from quack.jobs.queue import enqueue_job
from quack.ops.registry import can_rollback, event_registry, registry_gaps
from quack.ops.seed import SeedRefusedError, seed_learners, write_learners
from quack.ops.status import collect_status
from tests.support.broken import BrokenRedis
from tests.support.resources import build_app_resources


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _count(db: SessionFactory, sql: str, **params: Any) -> int:
    async with db() as session:
        return int((await session.execute(text(sql), params)).scalar_one())


_SEEDED_USERS = "SELECT COUNT(*) FROM auth.users WHERE email LIKE 'loadtest+%@quack.invalid'"
_SEEDED_EVENTS = (
    "SELECT COUNT(*) FROM core.events e JOIN auth.users u ON u.id = e.learner_id "
    "WHERE u.email LIKE 'loadtest+%@quack.invalid'"
)


async def test_seed_learners(
    db: SessionFactory,
    settings: Settings,
    pg_engine: AsyncEngine,
    neo4j: AsyncDriver,
    redis: Redis,
    tmp_path: Path,
) -> None:
    learners = await seed_learners(db, settings, count=3)
    out = tmp_path / "learners.json"
    write_learners(out, learners)
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [sorted(row) for row in rows] == [["domain_id", "learner_id", "token", "topic_ids"]] * 3
    assert await _count(db, _SEEDED_USERS) == 3
    for row in rows:
        learner = UUID(row["learner_id"])
        [domain] = await _q(
            db, "SELECT id, status FROM core.domains WHERE learner_id = :l", l=learner
        )
        assert str(domain["id"]) == row["domain_id"]
        assert len(row["topic_ids"]) == 3
        actors = await _q(
            db, "SELECT DISTINCT actor FROM core.events WHERE learner_id = :l", l=learner
        )
        assert actors == [{"actor": "system"}]

    # After the projector the topics are in Neo4j.
    projector = ProjectorWorker(settings, pg_engine, db, neo4j, None)
    while await projector.run_once():
        pass
    first = rows[0]
    async with neo4j.session() as session:
        result = await session.run(
            "MATCH (t:Topic) WHERE t.learnerId = $l RETURN count(t) AS n", l=first["learner_id"]
        )
        record = await result.single()
    assert record is not None and record["n"] == 3

    # The tokens pass GET /v1/auth/me.
    app = create_app(
        settings,
        resources=build_app_resources(settings, pg_engine, neo4j, redis, session_factory=db),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for row in rows:
            me = await client.get(
                "/v1/auth/me", headers={"Authorization": f"Bearer {row['token']}"}
            )
            assert me.status_code == 200, me.text

    # A second run with the same N duplicates nothing.
    events = await _count(db, _SEEDED_EVENTS)
    again = await seed_learners(db, settings, count=3)
    assert [x.learner_id for x in again] == [x.learner_id for x in learners]
    assert await _count(db, _SEEDED_USERS) == 3
    assert await _count(db, _SEEDED_EVENTS) == events


async def test_seed_learners_refuses_on_prod(
    db: SessionFactory, settings: Settings, tmp_path: Path
) -> None:
    prod = settings.model_copy(update={"env": "prod"})
    with pytest.raises(SeedRefusedError):
        await seed_learners(db, prod, count=1)
    # A real user (without preferences.seeded) is present: refused even with --confirm.
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO auth.users (id, email, password_hash) "
                "VALUES (gen_random_uuid(), 'real@example.com', 'x')"
            )
        )
    with pytest.raises(SeedRefusedError):
        await seed_learners(db, prod, count=1, confirm=True)
    assert await _count(db, _SEEDED_USERS) == 0

    # The CLI maps the refusal to exit code 2.
    out = tmp_path / "x.json"
    parser = cli.build_parser()
    for extra in ([], ["--confirm"]):
        args = parser.parse_args(
            ["ops", "seed-learners", "--count", "1", "--out", str(out), *extra]
        )
        assert await cli._ops(prod, args) == 2
    assert not out.exists()


# --- T-O4: CLI of Phase 3 --------------------------------------------------------------


def test_cli_parser_phase3(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """§6.7, WP8.1: process roles and the ops commands with their arguments; `worker` (both
    roles in one process, development only) refuses to start on prod with exit code 2."""
    parser = cli.build_parser()
    for argv in (["api"], ["projector"], ["workers"], ["worker"]):
        assert parser.parse_args(argv).command == argv[0]
    seed = parser.parse_args(
        ["ops", "seed-learners", "--count", "3", "--out", "l.json", "--fixture", "x", "--confirm"]
    )
    assert (seed.op, seed.count, seed.out, seed.fixture, seed.confirm) == (
        "seed-learners",
        3,
        "l.json",
        "x",
        True,
    )
    assert parser.parse_args(["ops", "openapi", "--out", "o.json"]).out == "o.json"
    assert parser.parse_args(["ops", "status"]).op == "status"
    assert parser.parse_args(["ops", "event-registry", "--out", "r.json"]).out == "r.json"
    rollback = parser.parse_args(["ops", "can-rollback", "--to-registry", "prev.json"])
    assert rollback.to_registry == "prev.json"
    assert cli.PROCESS_ROLES == {
        "api": "api",
        "projector": "projector",
        "workers": "workers",
        "worker": "workers",
        "ops": "ops",
    }

    monkeypatch.setattr(cli, "get_settings", lambda: settings.model_copy(update={"env": "prod"}))
    assert cli.main(["worker"]) == 2


def test_cli_binds_process_role_and_version(settings: Settings) -> None:
    """WP8.1 (Д-§9.1): the role goes into Settings before resources, every log line carries
    `process` and `version`."""
    import structlog

    configured = cli.process_settings(
        settings.model_copy(update={"quack_version": "1.2.3"}), "workers"
    )
    assert configured.process_role == "workers"
    try:
        bound = structlog.contextvars.get_contextvars()
        assert (bound["process"], bound["version"]) == ("workers", "1.2.3")
    finally:
        structlog.contextvars.unbind_contextvars("process", "version")


def test_cli_sets_process_role_before_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WP8.1, WP9.2: `Settings` is validated with the role of the command — prod secrets are
    those of the role (Д-§7), so `migrate` and `projector` start without JWT/Meta secrets."""
    import structlog

    monkeypatch.setenv("PROCESS_ROLE", "api")
    seen: list[str] = []

    def fake_settings() -> Settings:
        import os

        seen.append(os.environ["PROCESS_ROLE"])
        return settings

    monkeypatch.setattr(cli, "get_settings", fake_settings)
    try:
        assert cli.main(["ops", "event-registry", "--out", str(tmp_path / "r.json")]) == 0
    finally:
        structlog.contextvars.unbind_contextvars("process", "version")
    assert seen == ["ops"]


def test_seed_learners_refuses_without_jwt_secret(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`ops` requires no secrets (Д-§7), so seeding checks JWT_SECRET itself: tokens signed
    with an empty key are refused (exit 2, nothing written)."""
    import structlog

    monkeypatch.setattr(
        cli, "get_settings", lambda: settings.model_copy(update={"jwt_secret": SecretStr("")})
    )
    out = tmp_path / "learners.json"
    try:
        assert cli.main(["ops", "seed-learners", "--count", "1", "--out", str(out)]) == 2
    finally:
        structlog.contextvars.unbind_contextvars("process", "version")
    assert not out.exists()


# --- T-O1: ops status ------------------------------------------------------------------


async def _append_n(db: SessionFactory, learner: UUID, n: int) -> int:
    async with db() as session, session.begin():
        stored = await append_events(
            session,
            learner,
            [
                NewEvent(
                    type="learner.preferences_updated",
                    payload={"preferences": {"n": i}},
                    actor="learner",
                    idempotency_key=f"prefs:{learner}:{i}",
                )
                for i in range(n)
            ],
            publish_wakeup=False,
        )
    return stored[-1].seq


async def _exec(db: SessionFactory, sql: str, **params: Any) -> None:
    async with db() as session, session.begin():
        await session.execute(text(sql), params)


async def _offsets(db: SessionFactory, learner: UUID, seq: int) -> None:
    await _exec(
        db,
        "INSERT INTO ops.projector_offsets (learner_id, applied_seq) VALUES (:l, :s)",
        l=learner,
        s=seq,
    )
    await _exec(
        db,
        "INSERT INTO ops.dispatcher_offsets (learner_id, last_dispatched_seq) VALUES (:l, :s)",
        l=learner,
        s=seq,
    )


async def test_ops_status_report(
    db: SessionFactory, settings: Settings, neo4j: AsyncDriver, redis: Redis
) -> None:
    lagging, fine = uuid4(), uuid4()
    last = await _append_n(db, lagging, 5)
    await _offsets(db, lagging, last - 3)
    await _offsets(db, fine, await _append_n(db, fine, 2))
    async with db() as session, session.begin():
        old = await enqueue_job(
            session,
            replace(
                kinds.session_sweep_inactive(), run_after=datetime.now(UTC) - timedelta(seconds=120)
            ),
        )
        await enqueue_job(session, kinds.graph_curate(lagging, uuid4(), {}, required_seq=last))
    assert old is not None
    await _exec(
        db,
        "INSERT INTO ops.dlq (id, source, kind, learner_id, seq, payload, error_type, "
        "error_message, created_at) VALUES (:id, 'projector', 'x', :l, 1, '{}', 'E', 'e', now())",
        id=uuid4(),
        l=lagging,
    )
    for status_, tokens in (("ok", 100), ("error", 10)):
        await _exec(
            db,
            "INSERT INTO ops.llm_ledger (id, role, prompt_version, prompt_hash, model, "
            "input_tokens, output_tokens, latency_ms, status, created_at) VALUES "
            "(:id, 'teacher', 1, 'h', 'm', :t, 1, 5, :s, now())",
            id=uuid4(),
            t=tokens,
            s=status_,
        )
    await redis.set(llm_pressure_key("muse-spark-1.3"), "x", ex=60)
    await redis.set(llm_circuit_key("muse-wonder-1.0"), "x", ex=60)

    report, code = await collect_status(settings, db, neo4j, redis)
    assert code == 0
    assert report["dependencies"] == {"postgres": "ok", "neo4j": "ok", "redis": "ok"}
    assert report["version"] == settings.quack_version and report["generated_at"]
    assert report["projector"] == {
        "learners_total": 2,
        "learners_lagging": 1,
        "max_lag_events": 3,
        "stopped_streams": 1,
    }
    assert report["dispatcher"] == {"learners_lagging": 1, "max_lag_events": 3}
    jobs = report["jobs"]
    assert jobs["by_status"] == {"queued": 2}
    assert jobs["oldest_queued_age_s"] >= 120
    assert jobs["waiting_for_projection"] == 1
    assert (jobs["finished_last_hour"], jobs["failed_last_hour"]) == (0, 0)
    assert jobs["failed_share_last_hour"] is None
    assert report["dlq"] == {"unreplayed": 1}
    assert report["llm"] == {
        "tokens_last_hour": 112,
        "tokens_last_day": 112,
        "errors_last_hour": 1,
        "pressure": ["muse-spark-1.3"],
        "circuit_open": ["muse-wonder-1.0"],
    }
    json.dumps(report)  # one JSON object

    broken, code = await collect_status(settings, db, neo4j, cast(Redis, BrokenRedis()))
    assert code == 0
    assert broken["dependencies"]["redis"] == "error"
    assert broken["llm"]["pressure"] is None and broken["llm"]["circuit_open"] is None
    assert broken["projector"]["learners_lagging"] == 1


async def test_ops_status_without_postgres_exits_2(
    settings: Settings, neo4j: AsyncDriver, redis: Redis
) -> None:
    def no_db() -> Any:
        raise OSError("postgres is down")

    report, code = await collect_status(settings, cast(Any, no_db), neo4j, redis)
    assert code == 2
    assert report["dependencies"]["postgres"] == "error"
    assert report["projector"]["learners_lagging"] is None


# --- T-O2, T-O6: event registry, rollback check -----------------------------------------

REGISTRY_FIXTURE = Path(__file__).parent / "fixtures" / "event_registry.json"


async def test_event_registry_and_can_rollback(
    db: SessionFactory, settings: Settings, tmp_path: Path
) -> None:
    out = tmp_path / "registry.json"
    assert cli.main(["ops", "event-registry", "--out", str(out)]) == 0
    current = json.loads(out.read_text(encoding="utf-8"))
    expected = json.loads(REGISTRY_FIXTURE.read_text(encoding="utf-8"))
    assert current == expected == event_registry()
    pairs = [(e["type"], e["schema_version"]) for e in current["events"]]
    assert pairs == sorted(pairs) and len(pairs) == 37
    assert ("turn.extracted", 2) in pairs and current["rules_version"] == "rules@2"

    assert can_rollback(current, set()) == {
        "can_rollback": True,
        "new_pairs": [],
        "new_pairs_seen": [],
        "rules_changed": False,
    }
    previous = {**current, "events": [e for e in current["events"] if e["schema_version"] == 1]}
    p = {"type": "turn.extracted", "schema_version": 2}
    unseen = can_rollback(previous, set())
    assert unseen["can_rollback"] is True and unseen["new_pairs"] == [p]
    seen = can_rollback(previous, {("turn.extracted", 2)})
    assert seen["can_rollback"] is False and seen["new_pairs_seen"] == [p]
    other_rules = can_rollback({**current, "rules_version": "rules@1"}, set())
    assert other_rules["rules_changed"] is True and other_rules["can_rollback"] is False

    # Through the CLI with ops.event_types_seen of the database: exit 0 / 1.
    await _exec(
        db,
        "INSERT INTO ops.event_types_seen (type, schema_version) VALUES ('turn.extracted', 2)",
    )
    previous_file = tmp_path / "previous.json"
    previous_file.write_text(json.dumps(previous), encoding="utf-8")
    parser = cli.build_parser()
    args = parser.parse_args(["ops", "can-rollback", "--to-registry", str(out)])
    args.previous = current
    assert await cli._ops(settings, args) == 0
    args = parser.parse_args(["ops", "can-rollback", "--to-registry", str(previous_file)])
    args.previous = previous
    assert await cli._ops(settings, args) == 1


def test_event_registry_has_handlers_and_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    assert registry_gaps() == []
    monkeypatch.setitem(EVENT_TYPE_VERSIONS, ("lesson.invented", 1), object())
    assert registry_gaps() == [
        "lesson.invented@1: no projector handler",
        "lesson.invented@1: no dispatcher rule and no 'no rule' mark",
    ]
