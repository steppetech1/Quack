"""A `code` answer (Т-§6.1 «По тестам», Т-§6.10): the tests run in the runner first, outside any
transaction; then `answer.submitted` and `verdict.recorded` are written in one transaction. The
runner down or busy writes nothing — the attempt does not count (law 8)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AnswerSubmitted
from quack.events.keys import verdict_key
from quack.events.log import NewEvent, append_events
from quack.events.values import LearnerAnswer, Verdict
from quack.practice.answers import DEFAULT_PART, help_in_session
from quack.practice.code_check import (
    CodeTestView,
    all_numbers,
    learner_views,
    outcome_of,
    run_cases,
    verdict_tests,
)
from quack.practice.code_tests import PythonTests, SqlTests
from quack.runner_client.client import RunnerClient


@dataclass(frozen=True)
class SubmittedCode:
    """`tests` — as the learner may see them (hidden ones reduced to number and status)."""

    answer_seq: int
    verdict: Verdict
    tests: list[CodeTestView]


async def submit_code(
    db: Db,
    runner: RunnerClient,
    *,
    learner_id: UUID,
    session_id: UUID,
    domain_id: UUID | None,
    task_id: UUID,
    tests: PythonTests | SqlTests,
    code: str,
    idempotency_key: str,
    settings: Settings,
    redis: Redis | None = None,
) -> SubmittedCode:
    """Run every test, then write the answer and its verdict. `RunnerUnavailableError` from the
    runner propagates before anything is written."""
    views = await run_cases(runner, tests, code, all_numbers(tests), settings)
    payload = AnswerSubmitted(
        task_id=task_id,
        answer=LearnerAnswer(kind="code", value=code),
        source="editor",
    )
    async with db.transaction():
        help_used = await help_in_session(
            db, learner_id=learner_id, session_id=session_id, task_id=task_id
        )
        [answer] = await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type="answer.submitted",
                    payload=payload,
                    actor="learner",
                    idempotency_key=idempotency_key,
                    domain_id=domain_id,
                    session_id=session_id,
                )
            ],
            redis=redis,
        )
        verdict = Verdict(
            answer_seq=answer.seq,
            part=DEFAULT_PART,
            outcome=outcome_of(views),
            method="tests",
            verification="code",
            help_step=help_used.hint_step,
            solution_shown=help_used.solution_requested,
            tests=verdict_tests(
                views,
                settings.code_test_detail_chars,
                tests.function if isinstance(tests, PythonTests) else None,
            ),
        )
        await append_events(
            db,
            learner_id,
            [
                NewEvent(
                    type="verdict.recorded",
                    payload=verdict.model_dump(mode="json"),
                    actor="code",
                    idempotency_key=verdict_key(answer.seq, DEFAULT_PART),
                    domain_id=domain_id,
                    session_id=session_id,
                    causation_seq=answer.seq,
                )
            ],
            redis=redis,
        )
    return SubmittedCode(answer.seq, verdict, learner_views(views))
