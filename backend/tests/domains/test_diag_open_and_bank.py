"""Task bank, open-answer evaluation and self-report in the diagnostic (T-018 D-3).

Driven through the public HTTP API of `/v1/domains/{id}/diagnostic/*` with the fake LLM provider.
The first diagnostic topic of `diag_domain` is `T2` (skill `S2`).
"""

# ruff: noqa: F811

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.domains.diagnostic import _find_bank_task
from quack.domains.schemas import DiagnosticOutput
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.llm.errors import ProviderUnavailableError
from tests.domains.conftest import FakeLLMProvider
from tests.domains.test_diag_state_from_events import (  # noqa: F401
    DiagDomain,
    _answer,
    _auth_headers,
    _events,
    _finish,
    _queue_question,
    _start,
    diag_domain,
)


async def _add_bank_task(
    db: SessionFactory,
    learner_id: UUID,
    domain_id: UUID,
    topic_id: UUID,
    skill_id: UUID,
    *,
    kind: str,
    prompt_text: str,
    reference_solution: dict[str, Any],
    difficulty: float = 0.5,
) -> UUID:
    """Put a task into `core.task_bank` the regular way: a `task.generated` event outside any
    diagnostic session (the projection inserts the row)."""
    task_id = uuid4()
    async with db() as session, session.begin():
        await append_events(
            session,
            learner_id,
            [
                NewEvent(
                    type="task.generated",
                    actor="code",
                    domain_id=domain_id,
                    idempotency_key=f"bank-task:{task_id}",
                    payload={
                        "task_id": str(task_id),
                        "domain_id": str(domain_id),
                        "topic_id": str(topic_id),
                        "family_id": f"bank_{task_id.hex[:8]}",
                        "difficulty": difficulty,
                        "kind": kind,
                        "prompt_text": prompt_text,
                        "reference_solution": reference_solution,
                        "target_skills": [{"skill": {"skill_id": str(skill_id)}, "weight": 1.0}],
                        "target_misconceptions": [],
                    },
                )
            ],
            publish_wakeup=False,
        )
    return task_id


def _session_events(events: list[Any], session_id: str, *types: str) -> list[Any]:
    return [e for e in events if str(e.session_id) == session_id and e.type in types]


async def test_start_uses_task_bank_row_without_task_generated(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    bank_id = await _add_bank_task(
        db,
        learner_id,
        domain_id,
        diag_domain.topics[1],
        diag_domain.skills[1],
        kind="choice",
        prompt_text="Bank question?",
        reference_solution={"option": "B", "answer": "B", "options": ["A", "B", "C"]},
        difficulty=0.7,
    )
    _queue_question(fake_llm_provider, "next")

    started = await _start(api_client, token, domain_id)
    question = started["next_question"]
    assert question["task_id"] == str(bank_id)
    assert question["prompt_text"] == "Bank question?"
    assert question["kind"] == "choice"
    assert question["options"] == ["A", "B", "C"]
    assert fake_llm_provider.calls == []

    events = await _events(db, learner_id)
    assert _session_events(events, started["session_id"], "task.generated") == []
    assigned = _session_events(events, started["session_id"], "task.assigned")
    assert [e.payload["task_id"] for e in assigned] == [str(bank_id)]

    # A retry of start rebuilds the same bank question without an LLM call.
    retry = await api_client.post(
        f"/v1/domains/{domain_id}/diagnostic/start",
        headers=_auth_headers(token),
        json={"idempotency_key": f"diag-start:{domain_id}"},
    )
    assert retry.status_code == 200, retry.text
    assert retry.json() == started

    # The bank choice question is graded by code (no `evaluate` call), keeps its tested depth,
    # and the next question (another topic, no bank row) is generated.
    resp = await _answer(api_client, token, domain_id, started["session_id"], bank_id, "b")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["is_correct"] is True
    assert body["score"] == 1.0
    assert len(fake_llm_provider.calls) == 1
    assert "**Mode:** generate" in fake_llm_provider.calls[0].prompt
    (answered,) = await _events(db, learner_id, "diagnostic.question_answered")
    assert answered.payload["tested_depth"] == pytest.approx(0.7)
    generated = _session_events(
        await _events(db, learner_id), started["session_id"], "task.generated"
    )
    assert [e.payload["task_id"] for e in generated] == [body["next_question"]["task_id"]]


async def test_bank_task_is_not_asked_twice_in_a_session(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    t1, t2, _ = diag_domain.topics
    s1, s2, _ = diag_domain.skills
    first = await _add_bank_task(
        db, learner_id, domain_id, t2, s2, kind="choice", prompt_text="Q-T2?",
        reference_solution={"option": "X", "options": ["X", "Y"]},
    )  # fmt: skip
    second = await _add_bank_task(
        db, learner_id, domain_id, t1, s1, kind="choice", prompt_text="Q-T1?",
        reference_solution={"option": "X", "options": ["X", "Y"]},
    )  # fmt: skip

    started = await _start(api_client, token, domain_id)
    assert started["next_question"]["task_id"] == str(first)
    # Wrong answer -> next topic is the prerequisite T1, whose bank row is used.
    resp = await _answer(api_client, token, domain_id, started["session_id"], first, "Y")
    assert resp.json()["next_question"]["task_id"] == str(second)
    assert fake_llm_provider.calls == []

    excluded = await _find_bank_task(db, learner_id, domain_id, t2, s2, [first])
    assert excluded is None


async def test_find_bank_task_prefers_skill_and_respects_exclusions(
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    db: SessionFactory,
) -> None:
    learner_id, _ = seeded_learner
    domain_id = diag_domain.domain_id
    t2 = diag_domain.topics[1]
    s1, s2, _ = diag_domain.skills
    ref = {"option": "A", "options": ["A", "B"]}
    other_skill = await _add_bank_task(
        db, learner_id, domain_id, t2, s1, kind="choice", prompt_text="o", reference_solution=ref
    )
    wanted = await _add_bank_task(
        db, learner_id, domain_id, t2, s2, kind="choice", prompt_text="w", reference_solution=ref
    )

    found = await _find_bank_task(db, learner_id, domain_id, t2, s2, [])
    assert found is not None and found.task_id == wanted and found.skill_id == s2
    found = await _find_bank_task(db, learner_id, domain_id, t2, s2, [wanted])
    assert found is not None and found.task_id == other_skill
    assert await _find_bank_task(db, learner_id, domain_id, t2, s2, [wanted, other_skill]) is None
    assert await _find_bank_task(db, uuid4(), domain_id, t2, s2, []) is None
    assert await _find_bank_task(db, learner_id, domain_id, diag_domain.topics[0], s2, []) is None


async def test_open_question_is_scored_by_diagnostic_evaluate(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    bank_id = await _add_bank_task(
        db,
        learner_id,
        domain_id,
        diag_domain.topics[1],
        diag_domain.skills[1],
        kind="open",
        prompt_text="Explain the hub idea.",
        reference_solution={"answer": "the hub idea"},
    )
    fake_llm_provider.enqueue(
        DiagnosticOutput(
            prompt_text="Explain the hub idea.",
            correct_answer="the hub idea",
            is_correct=True,
            score=0.8,
            tested_depth=0.3,
        )
    )
    _queue_question(fake_llm_provider, "next")

    started = await _start(api_client, token, domain_id)
    assert started["next_question"]["kind"] == "open"
    session_id = started["session_id"]
    resp = await api_client.post(
        f"/v1/domains/{domain_id}/diagnostic/answer",
        headers=_auth_headers(token),
        json={
            "session_id": session_id,
            "task_id": str(bank_id),
            "answer": "it connects everything",
            "idempotency_key": "open-answer-1",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["score"] == pytest.approx(0.8)
    assert resp.json()["is_correct"] is True

    evaluate_call = fake_llm_provider.calls[0]
    assert "**Mode:** evaluate" in evaluate_call.prompt
    assert "Explain the hub idea." in evaluate_call.prompt
    assert "it connects everything" in evaluate_call.prompt
    async with db() as session:
        row = (
            await session.execute(
                text(
                    "SELECT status FROM core.llm_outbox WHERE learner_id = :l "
                    "AND idempotency_key = :k"
                ),
                {"l": learner_id, "k": f"diag:eval:{session_id}:open-answer-1"},
            )
        ).first()
    assert row is not None and row.status == "completed"

    (answered,) = await _events(db, learner_id, "diagnostic.question_answered")
    assert answered.payload["score"] == pytest.approx(0.8)
    assert answered.payload["tested_depth"] == pytest.approx(0.5)  # the question's, not the eval's

    finished = await _finish(api_client, token, domain_id, session_id)
    priors = {p["skill_id"]: p for p in finished.json()["inferred_priors"]}
    assert priors[str(diag_domain.skills[1])]["source"] == "tested"


@pytest.mark.parametrize(
    ("confidence", "answer", "expected_score", "expected_correct"),
    [
        (0.7, "my answer", 0.7, True),
        (0.4, "my answer", 0.4, False),
        (None, "my answer", 0.5, False),
        (None, "", 0.0, False),
    ],
)
async def test_open_question_falls_back_to_self_report_when_llm_unavailable(
    confidence: float | None,
    answer: str,
    expected_score: float,
    expected_correct: bool,
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    bank_id = await _add_bank_task(
        db,
        learner_id,
        domain_id,
        diag_domain.topics[1],
        diag_domain.skills[1],
        kind="open",
        prompt_text="Explain the hub idea.",
        reference_solution={"answer": "the hub idea"},
    )
    fake_llm_provider.enqueue(ProviderUnavailableError("503"))
    _queue_question(fake_llm_provider, "next")

    started = await _start(api_client, token, domain_id)
    session_id = started["session_id"]
    resp = await api_client.post(
        f"/v1/domains/{domain_id}/diagnostic/answer",
        headers=_auth_headers(token),
        json={
            "session_id": session_id,
            "task_id": str(bank_id),
            "answer": answer,
            "confidence_self_report": confidence,
            "idempotency_key": "open-answer-down",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["score"] == pytest.approx(expected_score)
    assert resp.json()["is_correct"] is expected_correct

    finished = await _finish(api_client, token, domain_id, session_id)
    assert finished.status_code == 200, finished.text
    priors = {p["skill_id"]: p for p in finished.json()["inferred_priors"]}
    tested = priors[str(diag_domain.skills[1])]
    assert tested["source"] == "self_report"
    assert tested["prior_mastery"] == pytest.approx(min(0.5, expected_score * 0.5))


async def test_question_answered_carries_generated_tested_depth(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    options = ["a", "b", "c"]
    fake_llm_provider.enqueue(
        DiagnosticOutput(
            prompt_text="Deep one?",
            options=options,
            correct_answer="b",
            tested_depth=0.8,
        ),
        DiagnosticOutput(
            prompt_text="Shallow one?",
            options=options,
            correct_answer="a",
            tested_depth=0.3,
        ),
    )

    started = await _start(api_client, token, domain_id)
    first_task = started["next_question"]["task_id"]
    resp = await _answer(api_client, token, domain_id, started["session_id"], first_task, "b")
    assert resp.status_code == 200, resp.text
    second_task = resp.json()["next_question"]["task_id"]
    resp = await _answer(api_client, token, domain_id, started["session_id"], second_task, "a")
    assert resp.status_code == 200, resp.text

    generated = await _events(db, learner_id, "task.generated")
    assert [e.actor for e in generated[:2]] == ["diagnostic@1", "diagnostic@1"]
    answered = await _events(db, learner_id, "diagnostic.question_answered")
    assert [e.payload["tested_depth"] for e in answered] == [
        pytest.approx(0.8),
        pytest.approx(0.3),
    ]


async def test_fallback_question_has_code_actor_and_default_depth(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    fake_llm_provider.enqueue(ProviderUnavailableError("503"))

    started = await _start(api_client, token, domain_id)
    resp = await _answer(
        api_client,
        token,
        domain_id,
        started["session_id"],
        started["next_question"]["task_id"],
        "A",
    )
    assert resp.status_code == 200, resp.text

    (generated, *_) = await _events(db, learner_id, "task.generated")
    assert generated.actor == "code"
    (answered,) = await _events(db, learner_id, "diagnostic.question_answered")
    assert answered.payload["tested_depth"] == pytest.approx(0.5)
    assert answered.payload["is_correct"] is True
