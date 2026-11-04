"""T-OPS (ТЗ Ф4 WP8.6 on Phase 4, Д-§11): `ops seed-learners` writes `core.learners` rows, the
structure events and sessions through `Db`; the token of the file is the session cookie."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from psycopg.abc import QueryNoTemplate as Query

from quack import cli
from quack.auth.sessions import resolve_session
from quack.config import Settings
from quack.db import Db
from quack.ops.seed import SeedRefusedError, load_fixture, seed_learners, write_learners
from tests.db.fixtures import create_learner

_SEEDED = "SELECT count(*) AS n FROM core.learners WHERE email LIKE 'loadtest+%%@quack.invalid'"
_EVENTS = (
    "SELECT type, count(*) AS n FROM core.events WHERE learner_id = %(learner_id)s "
    "GROUP BY type ORDER BY type"
)
_ALL_EVENTS = "SELECT count(*) AS n FROM core.events"
_ACTORS = "SELECT DISTINCT actor FROM core.events WHERE learner_id = %(learner_id)s"
_SESSIONS = "SELECT count(*) AS n FROM core.auth_sessions"


async def _n(db: Db, query: Query) -> int:
    row = await db.fetch_one(query)
    assert row is not None
    return int(row["n"])


class _Shared:
    def __init__(self, db: Db) -> None:
        self._db = db

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)

    async def close(self) -> None: ...


async def test_seed_learners(pg_db: Db, settings: Settings, tmp_path: Path) -> None:
    fixture = load_fixture()
    topics = len(fixture["topics"])
    skills = sum(len(t["skills"]) for t in fixture["topics"])

    learners = await seed_learners(pg_db, settings, count=3)

    out = tmp_path / "learners.json"
    write_learners(out, learners)
    rows = json.loads(out.read_text(encoding="utf-8"))
    assert [sorted(row) for row in rows] == [["domain_id", "learner_id", "token", "topic_ids"]] * 3
    assert await _n(pg_db, _SEEDED) == 3
    for row in rows:
        learner_id = UUID(row["learner_id"])
        counts = {
            r["type"]: r["n"] for r in await pg_db.fetch_all(_EVENTS, {"learner_id": learner_id})
        }
        assert counts == {"domain.created": 1, "skill.created": skills, "topic.created": topics}
        assert await pg_db.fetch_all(_ACTORS, {"learner_id": learner_id}) == [{"actor": "system"}]
        assert len(row["topic_ids"]) == topics
        # The token is a session (ТЗ Ф4 WP18.3): it resolves to the learner.
        session = await resolve_session(pg_db, token=row["token"], now=datetime.now(UTC))
        assert session is not None and session.learner_id == learner_id

    # A second run with the same N creates no learner and no event; it opens new sessions.
    events, sessions = await _n(pg_db, _ALL_EVENTS), await _n(pg_db, _SESSIONS)
    again = await seed_learners(pg_db, settings, count=3)
    assert [x.learner_id for x in again] == [x.learner_id for x in learners]
    assert await _n(pg_db, _SEEDED) == 3
    assert await _n(pg_db, _ALL_EVENTS) == events
    assert await _n(pg_db, _SESSIONS) == sessions + 3


async def test_seed_learners_refuses_on_prod(
    pg_db: Db, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def open_db(settings: Settings, **_: Any) -> _Shared:
        return _Shared(pg_db)

    monkeypatch.setattr(cli, "open_db", open_db)
    prod = settings.model_copy(update={"env": "prod"})
    with pytest.raises(SeedRefusedError):
        await seed_learners(pg_db, prod, count=1)  # no --confirm
    await create_learner(pg_db, email="real@example.com")  # a real learner is present
    with pytest.raises(SeedRefusedError):
        await seed_learners(pg_db, prod, count=1, confirm=True)
    assert await _n(pg_db, _SEEDED) == 0

    # The command maps the refusal to exit code 2 and writes no file.
    out = tmp_path / "x.json"
    parser = cli.build_parser()
    for extra in ([], ["--confirm"]):
        args = parser.parse_args(
            ["ops", "seed-learners", "--count", "1", "--out", str(out), *extra]
        )
        assert await cli._ops(prod, args) == 2  # pyright: ignore[reportPrivateUsage]
    assert not out.exists()


async def test_seed_on_prod_with_confirm_before_real_learners(
    pg_db: Db, settings: Settings
) -> None:
    prod = settings.model_copy(update={"env": "prod"})
    learners = await seed_learners(pg_db, prod, count=2, confirm=True)
    assert len(learners) == 2
    # Seeded learners alone do not count as real ones: the repeat is allowed.
    assert len(await seed_learners(pg_db, prod, count=2, confirm=True)) == 2


async def test_the_command_writes_the_file(
    pg_db: Db, settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def open_db(settings: Settings, **_: Any) -> _Shared:
        return _Shared(pg_db)

    monkeypatch.setattr(cli, "open_db", open_db)
    out = tmp_path / "learners.json"
    args = cli.build_parser().parse_args(
        ["ops", "seed-learners", "--count", "2", "--out", str(out)]
    )
    assert await cli._ops(settings, args) == 0  # pyright: ignore[reportPrivateUsage]
    assert len(json.loads(out.read_text(encoding="utf-8"))) == 2
