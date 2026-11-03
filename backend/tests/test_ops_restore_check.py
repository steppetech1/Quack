"""`ops restore-check` (ТЗ Фазы 4 WP9.6, Д-§8.3): the graph must not be ahead of the event log."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from neo4j import AsyncDriver
from sqlalchemy import text

from quack import cli
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.ops.restore_check import Violation, find_violations, restore_check


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


async def test_restore_check_compares_postgres_with_neo4j(db: SessionFactory, neo4j: AsyncDriver):
    ok, ahead, ghost = str(uuid4()), str(uuid4()), str(uuid4())
    async with db() as session:
        await session.execute(
            text(
                "INSERT INTO core.learner_seq (learner_id, last_seq) VALUES "
                "(CAST(:ok AS uuid), 7), (CAST(:ahead AS uuid), 7)"
            ),
            {"ok": ok, "ahead": ahead},
        )
        await session.commit()
    async with neo4j.session() as session:
        await session.run(
            "UNWIND $rows AS r CREATE (:Learner {id: r.id, appliedSeq: r.seq})",
            rows=[
                {"id": ok, "seq": 7},
                {"id": ahead, "seq": 9},
                {"id": ghost, "seq": 2},
            ],
        )

    violations = await restore_check(db, neo4j)

    assert sorted(v.learner_id for v in violations) == sorted([ahead, ghost])
    by_id = {v.learner_id: v for v in violations}
    assert (by_id[ahead].applied_seq, by_id[ahead].log_seq) == (9, 7)
    assert (by_id[ghost].applied_seq, by_id[ghost].log_seq) == (2, 0)


async def test_cli_prints_the_verdict_and_exits_non_zero_on_a_violation(
    db: SessionFactory,
    neo4j: AsyncDriver,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
):
    ahead = str(uuid4())
    async with neo4j.session() as session:
        await session.run("CREATE (:Learner {id: $id, appliedSeq: 5})", id=ahead)

    async def fake_resources(settings: Settings, fn, *, redis: bool = True) -> int:
        return await fn(None, db, neo4j, None)

    monkeypatch.setattr(cli, "_with_resources", fake_resources)

    code = await cli._restore_check(None, db, neo4j, None)  # pyright: ignore[reportPrivateUsage,reportUnknownArgumentType]

    report = json.loads(capsys.readouterr().out)
    assert code == 1
    assert report == {
        "ok": False,
        "violations": [{"learner_id": ahead, "applied_seq": 5, "log_seq": 0}],
    }
