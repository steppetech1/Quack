"""Spoiler guard of the teacher turn (ТЗ Фазы 3 §8.6d, WP15)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
from structlog.testing import capture_logs

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.practice.evaluation import TaskToCheck
from quack.session.schemas import TeacherOutput
from quack.session.spoiler_guard import find_spoiler
from tests.factories import new_event
from tests.session.fixtures import FakeLLMProvider, SeededDomain
from tests.session.helpers import parse_sse, task_label

pytest_plugins = ["tests.session.fixtures"]

# Reference of the first math task of `seeded_domain` is `3*x**2`.
SPOILER = "The derivative is 3x^2."
STUDENT_TEXT = "my private attempt text"


async def _start(
    client: httpx.AsyncClient, headers: dict[str, str], seed: SeededDomain, key: str
) -> UUID:
    response = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(seed.domain_id),
            "topic_id": str(seed.topic_id),
            "idempotency_key": key,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return UUID(response.json()["session_id"])


async def _turn(
    client: httpx.AsyncClient, headers: dict[str, str], session_id: UUID, key: str
) -> list[tuple[str, dict[str, Any]]]:
    response = await client.post(
        f"/v1/sessions/{session_id}/turns",
        json={"text": STUDENT_TEXT, "idempotency_key": key},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return parse_sse(response.text)


async def _assign(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    db: SessionFactory,
    seed: SeededDomain,
    fake_llm: FakeLLMProvider,
    session_id: UUID,
    task_id: UUID,
    key: str,
) -> None:
    label = await task_label(db, seed.learner_id, seed.topic_id, session_id, task_id)
    fake_llm.push(
        "teacher",
        TeacherOutput(
            reply_text="Try this task.", pedagogical_move="assign_task", assign_task_label=label
        ),
    )
    data = dict(await _turn(client, headers, session_id, key))
    assert UUID(data["task_assigned"]["task_id"]) == task_id


def _reply(text: str) -> TeacherOutput:
    return TeacherOutput(reply_text=text, pedagogical_move="explain")


def _token_text(sse: list[tuple[str, dict[str, Any]]]) -> str:
    return "".join(data["delta"] for name, data in sse if name == "token")


async def test_guard_retries_then_removes_sentence(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    fake_llm: FakeLLMProvider,
) -> None:
    """T-G1: an active task with a reference; the provider gives the answer three times in a
    row → exactly 3 calls, the final text without the answer sentence, the rest kept; a
    `guard_violation` record in the log without the learner's text."""
    session_id = await _start(client, auth_headers, seeded_domain, "guard-g1")
    task = seeded_domain.math_task_ids[0]
    await _assign(client, auth_headers, db, seeded_domain, fake_llm, session_id, task, "g1-a")
    before = len(fake_llm.calls_for("teacher"))

    fake_llm.push("teacher", *(_reply(f"Good effort. {SPOILER}") for _ in range(3)))
    with capture_logs() as logs:
        sse = await _turn(client, auth_headers, session_id, "g1-b")

    calls = fake_llm.calls_for("teacher")[before:]
    assert len(calls) == 3
    # Т-§12.1 «возврат в model с причиной»: the retries carry the reason, the first try not.
    reason = '{"rule": "spoiler"'
    assert reason not in calls[0].prompt
    assert all(reason in call.prompt for call in calls[1:])
    text = _token_text(sse)
    assert "3x^2" not in text
    assert "Good effort." in text
    violations = [entry for entry in logs if entry.get("event") == "guard_violation"]
    assert violations
    assert all(STUDENT_TEXT not in repr(entry) for entry in violations)


def _math_task(reference: str) -> TaskToCheck:
    return TaskToCheck(
        task_id=uuid4(),
        kind="math",
        prompt_text="Solve",
        reference_solution={"expression": reference},
        target_skills=[],
        target_misconceptions=[],
        difficulty=0.5,
        topic_id=uuid4(),
        family_id="f",
    )


def test_guard_ignores_small_numbers_and_single_letters(settings: Settings) -> None:
    """T-G2: reference «7» or «x» → a text with «7» and «x» is not a violation; reference
    «42» → it is."""
    text = "So x equals 7, and 42 is the answer."
    assert find_spoiler(text, [_math_task("7")], settings) is None
    assert find_spoiler(text, [_math_task("x")], settings) is None
    task_42 = _math_task("42")
    assert find_spoiler(text, [task_42], settings) is task_42


async def test_guard_inactive_when_not_applicable(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    fake_llm: FakeLLMProvider,
) -> None:
    """T-G3: no active task, the task is closed or the learner asked for the solution → a text
    with the answer passes without retries."""
    learner = seeded_domain.learner_id
    math1, math2 = seeded_domain.math_task_ids

    # No active task in the session.
    session_id = await _start(client, auth_headers, seeded_domain, "guard-g3")
    fake_llm.push("teacher", _reply(SPOILER))
    sse = await _turn(client, auth_headers, session_id, "g3-none")
    assert _token_text(sse) == SPOILER

    # The learner asked for the solution: a hint of the highest level.
    await _assign(client, auth_headers, db, seeded_domain, fake_llm, session_id, math1, "g3-a1")
    async with db() as session, session.begin():
        await append_events(
            session,
            learner,
            [
                new_event(
                    "task.hint_given",
                    {
                        "session_id": str(session_id),
                        "turn_id": str(uuid4()),
                        "task_id": str(math1),
                        "hint_level": 3,
                        "hint_text": "c",
                        "independence_penalty": 0.6,
                    },
                    session_id=session_id,
                )
            ],
            publish_wakeup=False,
        )
    before = len(fake_llm.calls_for("teacher"))
    fake_llm.push("teacher", _reply(SPOILER))
    sse = await _turn(client, auth_headers, session_id, "g3-hint")
    assert _token_text(sse) == SPOILER
    assert len(fake_llm.calls_for("teacher")) - before == 1

    # The task is closed: a correct `task.evaluated`.
    await _assign(client, auth_headers, db, seeded_domain, fake_llm, session_id, math2, "g3-a2")
    async with db() as session, session.begin():
        await append_events(
            session,
            learner,
            [
                new_event(
                    "task.evaluated",
                    {
                        "session_id": str(session_id),
                        "turn_id": str(uuid4()),
                        "task_id": str(math2),
                        "evaluator": "compute",
                        "is_correct": True,
                        "score": 1.0,
                        "independence": 1.0,
                        "feedback": "",
                        "skill_scores": [],
                    },
                    session_id=session_id,
                )
            ],
            publish_wakeup=False,
        )
    closed_spoiler = "It is 2cos(2x)."
    before = len(fake_llm.calls_for("teacher"))
    fake_llm.push("teacher", _reply(closed_spoiler))
    sse = await _turn(client, auth_headers, session_id, "g3-closed")
    assert _token_text(sse) == closed_spoiler
    assert len(fake_llm.calls_for("teacher")) - before == 1


async def test_guard_runs_before_first_token(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    seeded_domain: SeededDomain,
    db: SessionFactory,
    fake_llm: FakeLLMProvider,
) -> None:
    """T-G4: no SSE `token` of any attempt dropped by the guard carries the answer; the first
    `token` comes after the check passed."""
    session_id = await _start(client, auth_headers, seeded_domain, "guard-g4")
    task = seeded_domain.math_task_ids[0]
    await _assign(client, auth_headers, db, seeded_domain, fake_llm, session_id, task, "g4-a")
    before = len(fake_llm.calls_for("teacher"))

    clean = "Think about how the exponent changes."
    fake_llm.push("teacher", _reply(SPOILER), _reply(f"Hint. {SPOILER}"), _reply(clean))
    sse = await _turn(client, auth_headers, session_id, "g4-b")

    assert len(fake_llm.calls_for("teacher")) - before == 3
    tokens = [data["delta"] for name, data in sse if name == "token"]
    assert tokens
    assert all("3x^2" not in delta for delta in tokens)
    assert "".join(tokens) == clean
    names = [name for name, _ in sse]
    assert names.index("token") > names.index("turn_started")
