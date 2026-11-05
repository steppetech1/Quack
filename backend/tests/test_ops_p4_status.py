"""T-OPS (ТЗ Ф4 WP9.5, WP10.4; Т-O1 of Phase 3 on the schema of Phase 4): `ops status` reads
`core.learners`, `ops.jobs`, `ops.projector_offsets`, `ops.projector_failures`, `ops.llm_usage`
through `Db`; the deployment check (`deploy.sh`, `Д-§6.1` step 8) calls it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack import cli
from quack.config import Settings
from quack.db import Db
from quack.db.redis import llm_circuit_key, llm_pressure_key
from quack.events.log import NewEvent, append_events
from quack.ops.status import collect_status
from tests.db.fixtures import create_learner
from tests.support.broken import BrokenRedis

_JOB = (
    "INSERT INTO ops.jobs (job_key, kind, learner_id, priority_class, required_seq, status, "
    "run_at, heartbeat_at) VALUES (%(key)s, 'x.y', %(learner_id)s, 'P2', %(required)s, "
    "%(status)s, %(run_at)s, %(heartbeat)s)"
)
_USAGE = (
    "INSERT INTO ops.llm_usage (role, model, input_tokens, output_tokens, cost_usd, status, "
    "created_at) VALUES ('teacher', 'm', %(tokens)s, 1, %(cost)s, %(status)s, %(at)s)"
)


async def _events(db: Db, learner_id: UUID, n: int) -> int:
    stored = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="domain.created",
                payload={"domain_id": str(uuid4()), "name": f"d{i}"},
                actor="learner",
                idempotency_key=f"d:{learner_id}:{i}",
            )
            for i in range(n)
        ],
    )
    return stored[-1].seq


async def _offsets(db: Db, learner_id: UUID, projected: int, dispatched: int) -> None:
    await db.execute(
        "INSERT INTO ops.projector_offsets (learner_id, seq) VALUES (%(learner_id)s, %(seq)s)",
        {"learner_id": learner_id, "seq": projected},
    )
    await db.execute(
        "INSERT INTO ops.dispatcher_offsets (learner_id, last_dispatched_seq) "
        "VALUES (%(learner_id)s, %(seq)s)",
        {"learner_id": learner_id, "seq": dispatched},
    )


async def _job(
    db: Db,
    status: str,
    *,
    learner_id: UUID | None = None,
    required: int | None = None,
    run_at: datetime | None = None,
    heartbeat: datetime | None = None,
) -> None:
    await db.execute(
        _JOB,
        {
            "key": str(uuid4()),
            "learner_id": learner_id,
            "required": required,
            "status": status,
            "run_at": run_at or datetime.now(UTC),
            "heartbeat": heartbeat,
        },
    )


class _Shared:
    """The session-wide test `Db` must survive the command: `close` does nothing."""

    def __init__(self, db: Db) -> None:
        self._db = db

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    async def close(self) -> None: ...


async def test_ops_status_report(
    pg_db: Db, settings: Settings, neo4j: AsyncDriver, redis: Redis
) -> None:
    now = datetime.now(UTC)
    lagging, fine = await create_learner(pg_db), await create_learner(pg_db)
    last = await _events(pg_db, lagging, 5)
    await _offsets(pg_db, lagging, projected=last - 3, dispatched=last - 3)
    assert await _events(pg_db, fine, 2) == 2
    await _offsets(pg_db, fine, projected=2, dispatched=2)
    await pg_db.execute(
        "INSERT INTO ops.projector_failures (learner_id, seq, error) "
        "VALUES (%(learner_id)s, 3, 'boom')",
        {"learner_id": lagging},
    )
    await _job(pg_db, "queued", run_at=now - timedelta(seconds=120))
    await _job(pg_db, "queued", learner_id=lagging, required=last)  # waits for the projector
    await _job(pg_db, "queued", learner_id=fine, required=2)  # the projector is there already
    for _ in range(2):
        await _job(pg_db, "succeeded", heartbeat=now - timedelta(minutes=5))
    await _job(pg_db, "failed", heartbeat=now - timedelta(minutes=5))
    await _job(pg_db, "succeeded", heartbeat=now - timedelta(hours=2))  # not in the last hour
    await _job(pg_db, "failed", heartbeat=now - timedelta(hours=2))
    for status_, tokens, cost, ago in (
        ("ok", 100, "0.25", timedelta(minutes=5)),
        ("error", 10, "0.50", timedelta(minutes=10)),
        ("ok", 50, "1.00", timedelta(hours=3)),  # the day, not the hour
        ("ok", 999, "9.00", timedelta(hours=30)),  # neither
    ):
        params = {"tokens": tokens, "cost": cost, "status": status_, "at": now - ago}
        await pg_db.execute(_USAGE, params)
    await redis.set(llm_pressure_key("muse-spark-1.3"), "x", ex=60)
    await redis.set(llm_circuit_key("muse-wonder-1.0"), "x", ex=60)

    report, code = await collect_status(settings, pg_db, neo4j, redis)

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
    assert jobs["by_status"] == {"queued": 3, "succeeded": 3, "failed": 2}
    assert jobs["oldest_queued_age_s"] >= 120
    assert jobs["waiting_for_projection"] == 1
    assert (jobs["finished_last_hour"], jobs["failed_last_hour"]) == (2, 1)
    assert jobs["failed_share_last_hour"] == pytest.approx(1 / 3)
    assert "dlq" not in report  # Phase 4 has no DLQ: a failed job stays in `ops.jobs`
    assert report["llm"] == {
        "tokens_last_hour": 112,
        "tokens_last_day": 163,
        "errors_last_hour": 1,
        "cost_last_hour_usd": pytest.approx(0.75),
        "cost_last_day_usd": pytest.approx(1.75),
        "pressure": ["muse-spark-1.3"],
        "circuit_open": ["muse-wonder-1.0"],
    }
    json.dumps(report)  # one JSON object

    broken, code = await collect_status(settings, pg_db, neo4j, cast(Redis, BrokenRedis()))
    assert code == 0
    assert broken["dependencies"]["redis"] == "error"
    assert broken["llm"]["pressure"] is None and broken["llm"]["circuit_open"] is None
    assert broken["projector"]["learners_lagging"] == 1


async def test_ops_status_of_an_empty_system(pg_db: Db, settings: Settings) -> None:
    report, code = await collect_status(settings, pg_db, None, None)
    assert code == 0
    assert report["dependencies"] == {"postgres": "ok", "neo4j": "error", "redis": "error"}
    assert report["projector"]["learners_total"] == 0
    assert report["jobs"]["by_status"] == {} and report["jobs"]["oldest_queued_age_s"] is None
    assert report["jobs"]["failed_share_last_hour"] is None
    assert report["llm"]["cost_last_day_usd"] == 0 and report["llm"]["tokens_last_day"] == 0


async def test_ops_status_without_postgres_exits_2(
    settings: Settings, neo4j: AsyncDriver, redis: Redis
) -> None:
    class Down:
        async def fetch_one(self, *args: Any, **kwargs: Any) -> Any:
            raise OSError("postgres is down")

        fetch_all = fetch_one

    report, code = await collect_status(settings, cast(Db, Down()), neo4j, redis)
    assert code == 2
    assert report["dependencies"]["postgres"] == "error"
    assert report["projector"]["learners_lagging"] is None
    assert report["llm"]["cost_last_day_usd"] is None


async def test_the_command_prints_one_json_object_and_exits_0(
    pg_db: Db,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`deploy.sh` waits for exit 0 of `ops status` on the schema of Phase 4 (Д-§6.1 step 8)."""

    async def open_db(settings: Settings, **_: Any) -> _Shared:
        return _Shared(pg_db)

    monkeypatch.setattr(cli, "open_db", open_db)
    args = cli.build_parser().parse_args(["ops", "status"])

    assert await cli._ops(settings, args) == 0  # pyright: ignore[reportPrivateUsage]

    report = json.loads(capsys.readouterr().out)
    assert report["dependencies"]["postgres"] == "ok"
    assert report["projector"]["learners_total"] == 0


async def test_internal_status_serves_the_report_of_ops_status(
    pg_db: Db, neo4j: AsyncDriver, redis: Redis, settings: Settings
) -> None:
    """WP9.5, Д-§9.1: `GET /internal/status` has the content of `ops status`; Caddy does not
    proxy it (`deploy/Caddyfile`), and the OpenAPI snapshot does not list it (D-C-212)."""
    import httpx
    from fastapi import FastAPI

    from quack.api.app import create_app
    from tests.support.resources import build_app_resources

    app: FastAPI = create_app(
        settings,
        resources=build_app_resources(settings, neo4j, redis, db=pg_db),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/internal/status")

    report = response.json()
    assert response.status_code == 200, response.text
    assert {"projector", "jobs", "llm", "version"} <= set(report)
    assert "/internal/status" not in app.openapi()["paths"]
