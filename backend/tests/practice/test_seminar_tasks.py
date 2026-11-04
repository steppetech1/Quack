"""Seminar tasks, WP12.5 / WP20.2 (`Т-§5.6`; #271, D-B-218): the statement of the material is
kept; an answer from the material that the check rejects makes the task `disputed` (kept); no
answer and a rejection — thrown away; a retry writes nothing new."""

from __future__ import annotations

from typing import cast
from uuid import uuid4

from quack.config import Settings
from quack.db.db import Db
from quack.llm.gateway import LLMGateway
from quack.practice.seminar_tasks import SeminarTask, create_seminar_task, seminar_task_id
from quack.practice.task_store import get_task
from tests.db.fixtures import create_learner
from tests.practice.test_task_generation import _draft, _Roles

SETTINGS = Settings()
SKILL = uuid4()


def _task(key: str, answer: str | None) -> SeminarTask:
    return SeminarTask(
        key=key,
        statement="Seminar 3, problem 2: compute 2 + 5 - 2",
        skill_id=SKILL,
        skill_name="Adding numbers",
        topic_name="Arithmetic",
        answer=answer,
        kind="number",
    )


async def test_seminar_verification_by_the_table(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    roles = _Roles(
        [_draft("5")],
        {
            "task_checker:s:agree": "5",
            "task_checker:s:wrong": "8",
            "task_arbiter:s:wrong": "8",
            "task_checker:s:none": "8",
            "task_arbiter:s:none": "8",
        },
    )
    gateway = cast(LLMGateway, roles)

    async def make(key: str, answer: str | None) -> object:
        return await create_seminar_task(
            pg_db, gateway, SETTINGS, _task(key, answer), learner_id=learner_id
        )

    agreed = await make("s:agree", "5")
    assert agreed == seminar_task_id("s:agree")
    assert await make("s:wrong", "7") == seminar_task_id("s:wrong")  # disputed, kept
    assert await make("s:none", None) is None  # no answer, rejected: dropped
    assert roles.calls[0]["hard"] is False
    assert [c for c in roles.calls if c["role"] == "task_generator"][-1]["hard"] is True

    doc = await get_task(pg_db, learner_id=learner_id, task_id=seminar_task_id("s:agree"))
    assert doc is not None and doc.source == "seminar"
    assert doc.condition.text.startswith("Seminar 3, problem 2")
    events = await pg_db.fetch_all(
        "SELECT payload->>'verification' AS v FROM core.events WHERE learner_id = %(l)s "
        "AND type = 'task.created' ORDER BY seq",
        {"l": learner_id},
    )
    assert [e["v"] for e in events] == ["checked", "disputed"]

    again = await make("s:agree", "5")
    assert again == agreed
    count = await pg_db.fetch_one(
        "SELECT count(*) AS n FROM core.events WHERE learner_id = %(l)s AND type = 'task.created'",
        {"l": learner_id},
    )
    assert count is not None and count["n"] == 2
