"""`ops restore-check` (ТЗ Фазы 4 WP9.6, Д-§8.3): the graph must not be ahead of the event log."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from neo4j import AsyncDriver

from quack import cli
from quack.config import Settings
from quack.db import Db
from quack.ops.restore_check import Violation, find_violations, restore_check
from tests.db.fixtures import create_learner


class _Shared:
    """The test resources outlive the command: `close` does nothing."""

    def __init__(self, resource: Any) -> None:
        self._resource = resource

    def __getattr__(self, name: str) -> Any:
        return getattr(self._resource, name)

    async def close(self) -> None: ...


async def _learner_at(db: Db, seq: int) -> UUID:
    learner_id = await create_learner(db)
    await db.execute(
        "UPDATE core.learners SET seq = %(seq)s WHERE learner_id = %(learner_id)s",
        {"seq": seq, "learner_id": learner_id},
    )
    return learner_id


def test_a_graph_behind_or_equal_to_the_log_is_fine():
    assert find_violations({"a": 10, "b": 5}, {"a": 10, "b": 3}) == []


def test_a_graph_ahead_of_the_log_is_a_violation():
    assert find_violations({"a": 10, "b": 5}, {"a": 11, "b": 5}) == [Violation("a", 11, 10)]


def test_a_learner_missing_from_the_log_counts_as_seq_zero():
    assert find_violations({}, {"ghost": 4}) == [Violation("ghost", 4, 0)]
    assert find_violations({}, {"fresh": 0}) == []


def test_a_learner_only_in_the_log_is_fine():
    assert find_violations({"a": 3}, {}) == []


def test_violations_are_listed_in_a_stable_order():
    found = find_violations({}, {"b": 1, "a": 2})
    assert [v.learner_id for v in found] == ["a", "b"]


async def test_restore_check_compares_postgres_with_neo4j(pg_db: Db, neo4j: AsyncDriver):
    ok, ahead = str(await _learner_at(pg_db, 7)), str(await _learner_at(pg_db, 7))
    ghost, fresh = str(uuid4()), str(uuid4())
    async with neo4j.session() as session:
        await session.run(
            "UNWIND $rows AS r CREATE (:Learner {id: r.id, projectedSeq: r.seq})",
            rows=[
                {"id": ok, "seq": 7},
                {"id": ahead, "seq": 9},
                {"id": ghost, "seq": 2},
                {"id": fresh, "seq": 0},
            ],
        )

    violations = await restore_check(pg_db, neo4j)

    assert sorted(v.learner_id for v in violations) == sorted([ahead, ghost])
    by_id = {v.learner_id: v for v in violations}
    assert (by_id[ahead].applied_seq, by_id[ahead].log_seq) == (9, 7)
    assert (by_id[ghost].applied_seq, by_id[ghost].log_seq) == (2, 0)


async def test_a_learner_node_without_a_watermark_is_not_a_violation(pg_db: Db, neo4j: AsyncDriver):
    """A node the projector has not committed to yet has no `projectedSeq`: absence is not zero
    ahead of the log, it is nothing to compare."""
    nameless = str(uuid4())
    async with neo4j.session() as session:
        await session.run("CREATE (:Learner {id: $id})", id=nameless)
    assert await restore_check(pg_db, neo4j) == []


async def test_the_command_prints_the_verdict_and_exits_non_zero_on_a_violation(
    pg_db: Db,
    neo4j: AsyncDriver,
    settings: Settings,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    ahead = str(uuid4())
    async with neo4j.session() as session:
        await session.run("CREATE (:Learner {id: $id, projectedSeq: 5})", id=ahead)

    async def open_db(settings: Settings, **_: Any) -> _Shared:
        return _Shared(pg_db)

    monkeypatch.setattr(cli, "open_db", open_db)
    monkeypatch.setattr(cli, "create_driver", lambda settings: _Shared(neo4j))
    args = cli.build_parser().parse_args(["ops", "restore-check"])

    code = await cli._ops(settings, args)  # pyright: ignore[reportPrivateUsage]

    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report == {
        "ok": False,
        "violations": [{"learner_id": ahead, "applied_seq": 5, "log_seq": 0}],
    }
