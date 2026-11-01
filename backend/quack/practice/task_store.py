"""Task document `content.tasks` and `task.created` in one transaction (Т-§5.1; ТЗ Ф4 WP12.1).

The document holds what the API needs at hand for an instant verdict (Т-§5.1): the condition,
the answer spec, the solution space and its version, the source; the markup lives in the event
and in Neo4j. Agents and material parsing call `create_task`; the teacher turn writes the event
of a task it made up together with `turn.committed` (WP2.2) — it builds the event with
`task_created_event` and stores the document with `insert_task_document` inside the same
transaction. Tasks belong to the learner (Т-§5.1): every query takes `learner_id`.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict

from quack.db.db import Db
from quack.events.event_types import TaskCreated
from quack.events.log import NewEvent, append_events
from quack.events.records import TaskMarkupRecord
from quack.events.values import AnswerSpec
from quack.practice.role_outputs import SolutionSpace, TaskCondition

type TaskSource = Literal["seminar", "generated", "material", "teacher"]
type Verification = Literal["unchecked", "checked", "disputed"]

_INSERT_TASK = (
    "INSERT INTO content.tasks (task_id, learner_id, domain_id, source, version, condition, "
    "answer_spec, solution_space, solution_space_version, created_seq) VALUES (%(task_id)s, "
    "%(learner_id)s, %(domain_id)s, %(source)s, %(version)s, %(condition)s, %(answer_spec)s, "
    "%(solution_space)s, %(solution_space_version)s, %(created_seq)s)"
)

_SELECT_TASK = (
    "SELECT task_id, learner_id, domain_id, source, version, condition, answer_spec, "
    "solution_space, solution_space_version, created_seq FROM content.tasks "
    "WHERE learner_id = %(learner_id)s AND task_id = %(task_id)s"
)


class TaskDocument(BaseModel):
    """A row of `content.tasks` (ТЗ Ф4 §6.1)."""

    model_config = ConfigDict(frozen=True)

    task_id: UUID
    learner_id: UUID
    domain_id: UUID | None
    source: TaskSource
    version: int
    condition: TaskCondition
    answer_spec: AnswerSpec
    solution_space: SolutionSpace | None
    solution_space_version: str | None
    created_seq: int


def task_created_event(
    *,
    task_id: UUID,
    family_id: UUID,
    source: TaskSource,
    markup: TaskMarkupRecord,
    verification: Verification,
    actor: str,
    idempotency_key: str,
    domain_id: UUID | None = None,
    session_id: UUID | None = None,
    causation_seq: int | None = None,
) -> NewEvent:
    """`task.created` (ТЗ Ф4 §6.3) of one task."""
    return NewEvent(
        type="task.created",
        payload=TaskCreated(
            task_id=task_id,
            family_id=family_id,
            source=source,
            markup=markup,
            verification=verification,
        ),
        actor=actor,
        idempotency_key=idempotency_key,
        domain_id=domain_id,
        session_id=session_id,
        causation_seq=causation_seq,
    )


async def insert_task_document(
    db: Db,
    *,
    learner_id: UUID,
    task_id: UUID,
    domain_id: UUID | None,
    source: TaskSource,
    condition: TaskCondition,
    answer_spec: AnswerSpec,
    created_seq: int,
    solution_space: SolutionSpace | None = None,
    solution_space_version: str | None = None,
    version: int = 1,
) -> None:
    """Store the document; call inside the transaction that wrote its `task.created`."""
    await db.execute(
        _INSERT_TASK,
        {
            "task_id": task_id,
            "learner_id": learner_id,
            "domain_id": domain_id,
            "source": source,
            "version": version,
            "condition": Jsonb(condition.model_dump(mode="json")),
            "answer_spec": Jsonb(answer_spec.model_dump(mode="json")),
            "solution_space": (
                Jsonb(solution_space.model_dump(mode="json")) if solution_space else None
            ),
            "solution_space_version": solution_space_version,
            "created_seq": created_seq,
        },
    )


async def create_task(
    db: Db,
    *,
    learner_id: UUID,
    task_id: UUID,
    family_id: UUID,
    source: TaskSource,
    condition: TaskCondition,
    markup: TaskMarkupRecord,
    verification: Verification,
    actor: str,
    idempotency_key: str,
    domain_id: UUID | None = None,
    session_id: UUID | None = None,
    causation_seq: int | None = None,
    solution_space: SolutionSpace | None = None,
    solution_space_version: str | None = None,
) -> int:
    """Т-§5.1: `task.created` and the document in one transaction; a failure writes neither.
    Returns the seq of `task.created`. A repeat with the same key and payload writes nothing
    new (law 5) and returns the seq of the first write."""
    event = task_created_event(
        task_id=task_id,
        family_id=family_id,
        source=source,
        markup=markup,
        verification=verification,
        actor=actor,
        idempotency_key=idempotency_key,
        domain_id=domain_id,
        session_id=session_id,
        causation_seq=causation_seq,
    )
    async with db.transaction():
        [stored] = await append_events(db, learner_id, [event])
        if await get_task(db, learner_id=learner_id, task_id=task_id) is None:
            await insert_task_document(
                db,
                learner_id=learner_id,
                task_id=task_id,
                domain_id=domain_id,
                source=source,
                condition=condition,
                answer_spec=markup.answer,
                created_seq=stored.seq,
                solution_space=solution_space,
                solution_space_version=solution_space_version,
            )
    return stored.seq


async def get_task(db: Db, *, learner_id: UUID, task_id: UUID) -> TaskDocument | None:
    """The document of a learner's task; another learner's task is `None` (Т-§3.5)."""
    return await db.fetch_one(
        _SELECT_TASK, {"learner_id": learner_id, "task_id": task_id}, row=TaskDocument
    )
