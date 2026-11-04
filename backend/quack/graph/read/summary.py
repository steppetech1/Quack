"""Итог сессии: состояние «до» и «после» в кодах (ТЗ Ф4 WP8.3, §2.3 строка 12; `Т-§2.6`, `П-4.10`).

The report is state data, not text (`П-4.10`): for every skill the session touched, the levels
(status and basis), the gap type and the consolidation flag **before** the session —
`GraphReader.state_at(learner, seq_n, skills)` with `seq_n` the seq before the first
`session.started` of the session (the events up to N replayed by the same handlers as the
rebuild; a late dispute does not rewrite the past, D-A-191) — and **after**, the current
projection. What was done (tasks, their closing) is read from the learner's journal.

The skills are the ones named by the events of this session: tasks of the turns, answers and
closings (their `tests` from `task.created` and its corrections), exposures, extracted
elements and their root causes, diagnoses, skills created in the session. Every read carries
the learner (law 1): another learner's session is «not found» (D-A-213).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from quack.db import Db
from quack.events.log import StoredEvent
from quack.events.values import GapType, Level, Outcome
from quack.graph.read import GraphReader
from quack.graph.read.models import LevelBasis, LevelStatus, SkillFact, SliceFocus

_SESSION_EVENTS = (
    "SELECT learner_id, seq, id, type, schema_version, actor, domain_id, session_id, "
    "causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, payload, "
    "source_refs FROM core.events WHERE learner_id = %(learner_id)s "
    "AND session_id = %(session_id)s ORDER BY seq"
)
# `task.created` and the corrections of the markup by the checker (`corrected_markup`).
_TASK_MARKUP = (
    "SELECT learner_id, seq, id, type, schema_version, actor, domain_id, session_id, "
    "causation_seq, correlation_id, idempotency_key, payload_hash, occurred_at, payload, "
    "source_refs FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type IN ('task.created', 'task.verified', 'task.key_disputed', 'task.markup_disputed') "
    "AND payload ->> 'task_id' = ANY(%(task_ids)s) ORDER BY seq"
)

type TaskClosing = Literal["solved", "skipped", "solution_shown"]


class SessionNotFoundError(LookupError):
    """No such session of this learner — a session of another learner looks the same."""


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LevelCode(_Out):
    level: Level
    status: LevelStatus
    basis: LevelBasis | None = None


class SkillCodes(_Out):
    """The state of a skill in codes — no names, no weights, no times."""

    levels: list[LevelCode]
    gap_type: GapType | None = None
    consolidation_flag: bool = False


class SkillChange(_Out):
    """`before` is `None` for a skill that did not exist before the session; `after` is `None`
    when the skill is no longer read under this id (merged away)."""

    skill_id: UUID
    topic_id: UUID | None = None
    before: SkillCodes | None = None
    after: SkillCodes | None = None
    changed: bool


class TaskDone(_Out):
    """A task of the session: answers given, the deepest hint step shown, the outcome of the
    last verdict and how it was closed (`None` — left open)."""

    task_id: UUID
    answers: int
    hint_step: int
    last_outcome: Outcome | None = None
    closed: TaskClosing | None = None


class SessionSummary(_Out):
    session_id: UUID
    domain_id: UUID
    intent: str
    topic_id: UUID | None = None
    hub_id: UUID | None = None
    started_seq: int
    before_seq: int
    ended_reason: str | None = None
    skills: list[SkillChange]
    tasks: list[TaskDone]


def _codes(fact: SkillFact) -> SkillCodes:
    return SkillCodes(
        levels=[LevelCode(level=x.level, status=x.status, basis=x.basis) for x in fact.levels],
        gap_type=fact.gap_type,
        consolidation_flag=fact.consolidation_flag,
    )


def _ids(*values: Any) -> Iterable[str]:
    for value in values:
        if value is not None:
            yield str(value)


def _tests(markup: dict[str, Any]) -> Iterable[str]:
    for test in markup.get("tests") or []:
        yield from _ids(test.get("skill_id"))
    for part in markup.get("parts") or []:
        for test in part.get("tests") or []:
            yield from _ids(test.get("skill_id"))


def _task_ids(events: Sequence[StoredEvent]) -> list[str]:
    """Tasks of the session: those a turn put on the screen, answered, hinted or closed."""
    found: dict[str, None] = {}
    for e in events:
        p = e.payload
        if e.type == "turn.committed":
            for task in p.get("markup", {}).get("tasks") or []:
                found.update(dict.fromkeys(_ids(task.get("task_id"))))
        elif e.type in ("answer.submitted", "task.closed", "hint.shown"):
            found.update(dict.fromkeys(_ids(p.get("task_id"))))
    return list(found)


def _skills_of_events(events: Sequence[StoredEvent]) -> Iterable[str]:
    for e in events:
        p = e.payload
        if e.type == "skill.created":
            yield from _ids(p.get("skill_id"))
        elif e.type == "turn.committed":
            for x in p.get("markup", {}).get("exposures") or []:
                yield from _ids(x.get("skill_id"))
            for task in p.get("markup", {}).get("tasks") or []:
                yield from _tests(task)
        elif e.type == "observation.extracted":
            for el in p.get("elements") or []:
                yield from _ids(el.get("skill_id"), (el.get("root_cause") or {}).get("skill_id"))
        elif e.type == "observation.diagnosed":
            yield from _ids((p.get("diagnosis") or {}).get("root_cause_skill_id"))


async def _skills_of_tasks(db: Db, learner_id: UUID, task_ids: list[str]) -> list[str]:
    if not task_ids:
        return []
    params = {"learner_id": learner_id, "task_ids": task_ids}
    markup: dict[str, dict[str, Any]] = {}
    for e in await db.fetch_all(_TASK_MARKUP, params, row=StoredEvent):
        # the latest correction of the checker wins over `task.created`
        corrected = e.payload.get("corrected_markup") if e.type != "task.created" else None
        current = e.payload.get("markup") if e.type == "task.created" else corrected
        if current is not None:
            markup[str(e.payload["task_id"])] = current
    return [skill for m in markup.values() for skill in _tests(m)]


def _tasks_done(events: Sequence[StoredEvent]) -> list[TaskDone]:
    answers: dict[int, str] = {}  # answer seq → task
    for e in events:
        if e.type == "answer.submitted":
            answers[e.seq] = str(e.payload["task_id"])
    count: dict[str, int] = {}
    hint: dict[str, int] = {}
    outcome: dict[str, Outcome] = {}
    closed: dict[str, TaskClosing] = {}
    for e in events:
        p = e.payload
        if e.type == "answer.submitted":
            count[str(p["task_id"])] = count.get(str(p["task_id"]), 0) + 1
        elif e.type == "hint.shown":
            task = str(p["task_id"])
            hint[task] = max(hint.get(task, 0), int(p["step"]))
        elif e.type == "verdict.recorded" and int(p["answer_seq"]) in answers:
            outcome[answers[int(p["answer_seq"])]] = p["outcome"]
        elif e.type == "task.closed":
            closed[str(p["task_id"])] = p["how"]
    return [
        TaskDone(
            task_id=UUID(task),
            answers=count.get(task, 0),
            hint_step=hint.get(task, 0),
            last_outcome=outcome.get(task),
            closed=closed.get(task),
        )
        for task in _task_ids(events)
    ]


async def session_summary(
    db: Db, reader: GraphReader, learner_id: UUID, session_id: UUID
) -> SessionSummary:
    """«До и после» of one session of the learner; `reader` is bound to the same learner and to
    `db` (`create_graph_reader(..., db=db)`). Raises `SessionNotFoundError` for a session that
    does not exist or belongs to another learner."""
    params = {"learner_id": learner_id, "session_id": session_id}
    events = await db.fetch_all(_SESSION_EVENTS, params, row=StoredEvent)
    started = next((e for e in events if e.type == "session.started"), None)
    if started is None:
        raise SessionNotFoundError(f"no session {session_id}")
    before_seq = started.seq - 1
    ended = next((e for e in reversed(events) if e.type == "session.ended"), None)
    skill_ids = list(
        dict.fromkeys(
            [
                *_skills_of_events(events),
                *await _skills_of_tasks(db, learner_id, _task_ids(events)),
            ]
        )
    )
    wanted = [UUID(s) for s in skill_ids]
    before = await reader.state_at(learner_id, before_seq, wanted) if wanted else None
    after = (
        await reader.slice_for_turn(learner_id, session_id, SliceFocus(skill_ids=wanted))
        if wanted
        else None
    )
    was = {f.skill_id: f for f in (before.skills if before else [])}
    now = {f.skill_id: f for f in (after.skills if after else [])}
    changes = []
    for skill in dict.fromkeys(UUID(s) for s in skill_ids):
        b, a = was.get(skill), now.get(skill)
        if b is None and a is None:
            continue  # named by an event but not in the graph (yet): nothing to compare
        codes_b, codes_a = (_codes(b) if b else None), (_codes(a) if a else None)
        known = a or b
        topic = known.topic_id if known is not None else None
        changes.append(
            SkillChange(
                skill_id=skill,
                topic_id=topic,
                before=codes_b,
                after=codes_a,
                changed=codes_b != codes_a,
            )
        )
    return SessionSummary(
        session_id=session_id,
        domain_id=UUID(str(started.payload["domain_id"])),
        intent=str(started.payload["intent"]),
        topic_id=_opt(started.payload.get("topic_id")),
        hub_id=_opt(started.payload.get("hub_id")),
        started_seq=started.seq,
        before_seq=before_seq,
        ended_reason=str(ended.payload["reason"]) if ended is not None else None,
        skills=changes,
        tasks=_tasks_done(events),
    )


def _opt(value: object) -> UUID | None:
    return None if value is None else UUID(str(value))
