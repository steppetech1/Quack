"""Operations (ТЗ Фазы 3 §8.6): T-O3 (`ops seed-learners`)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import pytest
from neo4j import AsyncDriver
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack import cli
from quack.api.app import create_app
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.graph.worker import ProjectorWorker
from quack.ops.seed import SeedRefusedError, seed_learners, write_learners
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
