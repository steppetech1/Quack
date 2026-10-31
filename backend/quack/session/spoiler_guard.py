"""Spoiler guard of the teacher turn (ТЗ Фазы 3 WP15; Т-§12.1, Т-§12.4, П-4.9).

The answer of an active task (shown in the session, not closed, the solution not requested)
must not appear in the teacher reply. The check runs on the full reply before the first SSE
`token`; on a violation the turn goes back to the model, then code removes the sentence.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from quack.config import Settings
from quack.practice.evaluation import TaskToCheck


def spoiler_found(text: str, task: TaskToCheck, settings: Settings) -> bool:
    """True if `text` contains the answer of `task` by its `reference_solution`."""
    raise NotImplementedError


def find_spoiler(text: str, tasks: Sequence[TaskToCheck], settings: Settings) -> TaskToCheck | None:
    """The first task whose answer is in `text`, or `None`."""
    raise NotImplementedError


def remove_spoiler_sentences(text: str, tasks: Sequence[TaskToCheck], settings: Settings) -> str:
    """`text` without the sentences that contain the answer of any of `tasks`."""
    raise NotImplementedError


async def guarded_tasks(
    session: AsyncSession,
    learner_id: UUID,
    session_id: UUID,
    settings: Settings,
    *,
    closed_now: UUID | None = None,
) -> list[TaskToCheck]:
    """Active tasks of the session: assigned before this turn, no correct `task.evaluated`,
    no hint of the highest level (the request for the solution, WP0 audit); `closed_now` —
    a task this turn has just evaluated as correct."""
    raise NotImplementedError
