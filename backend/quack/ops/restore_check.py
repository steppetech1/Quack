"""`ops restore-check` (Д-§8.3, Т-§20.4, ТЗ Фазы 4 WP9.6).

After a restore from a dump or a disk snapshot Neo4j can be ahead of the event log: the graph saw
events that the restored log no longer has, and new events would get the same numbers. The check
compares, for every learner, `appliedSeq` in Neo4j with `last_seq` in `core.learner_seq`. A learner
with `appliedSeq` above the log (or absent from the log) is a violation: the exit code is not 0 and
the list is printed; the cure is `ops rebuild --all` (Д-§8.7). A Neo4j that lags is not a violation,
the projector catches up by itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from neo4j import AsyncDriver
from sqlalchemy import text

from quack.db.neo4j import read_session
from quack.db.postgres import SessionFactory


@dataclass(frozen=True)
class Violation:
    learner_id: str
    applied_seq: int
    log_seq: int


async def log_seqs(session_factory: SessionFactory) -> dict[str, int]:
    async with session_factory() as session:
        rows = await session.execute(text("SELECT learner_id, last_seq FROM core.learner_seq"))
        return {str(row.learner_id): int(row.last_seq) for row in rows}


async def graph_seqs(driver: AsyncDriver) -> dict[str, int]:
    async with read_session(driver) as session:
        result = await session.run(
            "MATCH (l:Learner) RETURN l.id AS id, coalesce(l.appliedSeq, 0) AS seq"
        )
        return {str(record["id"]): int(record["seq"]) async for record in result}


def find_violations(log: dict[str, int], graph: dict[str, int]) -> list[Violation]:
    """Learners whose graph is ahead of the log; a learner missing from the log has seq 0."""
    return [
        Violation(learner_id, applied, log.get(learner_id, 0))
        for learner_id, applied in sorted(graph.items())
        if applied > log.get(learner_id, 0)
    ]


async def restore_check(session_factory: SessionFactory, driver: AsyncDriver) -> list[Violation]:
    return find_violations(await log_seqs(session_factory), await graph_seqs(driver))
