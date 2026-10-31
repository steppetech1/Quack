"""Task pick from the bank — one place for the turn and for `tasks/next` (WP6.2).

Two picks with different inputs, both moved without changing their queries:

- `pick_task` — by topic, for a degraded turn and a teacher assignment without a valid
  task (ТЗ-П2 §2.3 п.5; moved from `quack/session/tasks.py`);
- `bank_task_for_skill` / `fallback_bank_task` / `excluded_families` — by target skill, for
  `POST .../tasks/next` (ТЗ-П3 §2.2 п.1, A7, A8; moved from `quack/artifacts/practice.py`).
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any
from uuid import UUID

import orjson
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from quack.practice.evaluation import TaskToCheck, task_from_row

_BANK_COLUMNS = ", ".join(
    f"b.{c}"
    for c in (
        "id",
        "topic_id",
        "family_id",
        "difficulty",
        "kind",
        "prompt_text",
        "reference_solution",
        "target_skills",
        "target_misconceptions",
    )
)

# Correlated conditions on a `core.task_bank b` row.
_ASSIGNED_IN_SESSION = (
    "EXISTS (SELECT 1 FROM core.events e WHERE e.learner_id = b.learner_id "
    "AND e.session_id = :s AND e.type = 'task.assigned' "
    "AND e.payload->>'task_id' = CAST(b.id AS TEXT))"
)
_EVALUATED = (
    "EXISTS (SELECT 1 FROM core.events e WHERE e.learner_id = b.learner_id "
    "AND e.type = 'task.evaluated' AND e.payload->>'task_id' = CAST(b.id AS TEXT))"
)
_NEW_FAMILY_FOR_SKILL = (
    "(b.target_skills @> CAST(:target AS JSONB) AND NOT (b.family_id = ANY(:excluded)))"
)
_SOLVED_FAMILY = (
    "EXISTS (SELECT 1 FROM core.events e JOIN core.task_bank t2 "
    "ON t2.learner_id = e.learner_id AND t2.id = (e.payload->>'task_id')::uuid "
    "WHERE e.learner_id = :l AND e.type = 'task.evaluated' "
    "AND (e.payload->>'is_correct')::boolean AND t2.family_id = b.family_id)"
)


def _json(value: Any) -> str:
    return orjson.dumps(value).decode()


def _target_pattern(skill_id: str) -> str:
    return _json([{"skill": {"skill_id": skill_id}}])


async def pick_task(
    session: AsyncSession,
    learner_id: UUID,
    topic_id: UUID,
    *,
    exclude_task_ids: Collection[UUID] = (),
    solved_family_ids: Collection[str] = (),
    kinds: Collection[str] | None = None,
    session_id: UUID | None = None,
) -> TaskToCheck | None:
    """Unassigned task of the topic, preferring families without a successful solve.

    `exclude_task_ids` is `overlay.assigned_task_ids`; with `session_id` the tasks already
    assigned in that session (projected or not) are excluded too. A family counts as solved
    if it is in `solved_family_ids` or the log has a correct `task.evaluated` of the family.
    """
    params: dict[str, Any] = {"l": learner_id, "t": topic_id}
    solved = _SOLVED_FAMILY
    if solved_family_ids:
        solved = f"(b.family_id = ANY(:solved) OR {_SOLVED_FAMILY})"
        params["solved"] = sorted(set(solved_family_ids))
    sql = (
        f"SELECT {_BANK_COLUMNS}, {solved} AS solved FROM core.task_bank b "
        "WHERE b.learner_id = :l AND b.topic_id = :t"
    )
    if exclude_task_ids:
        sql += " AND NOT (b.id = ANY(:exclude))"
        params["exclude"] = sorted(set(exclude_task_ids), key=str)
    if kinds is not None:
        sql += " AND b.kind = ANY(:kinds)"
        params["kinds"] = sorted(set(kinds))
    if session_id is not None:
        sql += f" AND NOT {_ASSIGNED_IN_SESSION}"
        params["s"] = session_id
    sql += " ORDER BY solved, b.difficulty, b.created_seq, b.id LIMIT 1"
    found = (await session.execute(text(sql), params)).first()
    return None if found is None else task_from_row(found)


async def excluded_families(session: AsyncSession, learner_id: UUID, skill_id: str) -> list[str]:
    """A8: families of tasks with a `task.evaluated` that scores this skill."""
    rows = (
        await session.execute(
            text(
                "SELECT DISTINCT b.family_id FROM core.events e JOIN core.task_bank b "
                "ON b.learner_id = e.learner_id AND CAST(b.id AS TEXT) = e.payload->>'task_id' "
                "WHERE e.learner_id = :l AND e.type = 'task.evaluated' "
                "AND e.payload->'skill_scores' @> CAST(:score AS JSONB) ORDER BY b.family_id"
            ),
            {"l": learner_id, "score": _json([{"skill_id": skill_id}])},
        )
    ).all()
    return [r.family_id for r in rows]


async def bank_task_for_skill(
    session: AsyncSession,
    learner_id: UUID,
    session_id: UUID,
    skill_id: str,
    kind: str,
    excluded: list[str],
) -> TaskToCheck | None:
    """ТЗ-П3 §2.2 п.1, A8: an unsolved task of a new family for the skill, earliest first."""
    row = (
        await session.execute(
            text(
                f"SELECT {_BANK_COLUMNS} FROM core.task_bank b "
                f"WHERE b.learner_id = :l AND b.kind = :kind AND {_NEW_FAMILY_FOR_SKILL} "
                f"AND NOT {_ASSIGNED_IN_SESSION} AND NOT {_EVALUATED} "
                "ORDER BY b.created_seq LIMIT 1"
            ),
            {
                "l": learner_id,
                "s": session_id,
                "kind": kind,
                "target": _target_pattern(skill_id),
                "excluded": excluded,
            },
        )
    ).first()
    return None if row is None else task_from_row(row)


async def fallback_bank_task(
    session: AsyncSession,
    learner_id: UUID,
    session_id: UUID,
    topic_id: UUID,
    skill_id: str,
    kind: str,
    excluded: list[str],
) -> TaskToCheck | None:
    """Any task of the topic and kind not assigned in the session: a new family for the
    skill first, then unsolved before solved, then by `created_seq` (Т-§22.7)."""
    row = (
        await session.execute(
            text(
                f"SELECT {_BANK_COLUMNS} FROM core.task_bank b "
                "WHERE b.learner_id = :l AND b.topic_id = :topic AND b.kind = :kind "
                f"AND NOT {_ASSIGNED_IN_SESSION} "
                f"ORDER BY {_NEW_FAMILY_FOR_SKILL} DESC, {_EVALUATED}, b.created_seq LIMIT 1"
            ),
            {
                "l": learner_id,
                "s": session_id,
                "topic": topic_id,
                "kind": kind,
                "target": _target_pattern(skill_id),
                "excluded": excluded,
            },
        )
    ).first()
    return None if row is None else task_from_row(row)
