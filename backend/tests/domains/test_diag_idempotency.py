"""Idempotent retries of `POST /v1/domains/{id}/diagnostic/start` and `/answer` (T-017 D-2).

Retries with the same `idempotency_key` must not call the LLM again, must not append events and
must return the stored response, even when the session has advanced since the first call.
"""

# ruff: noqa: F811

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import httpx

from quack.db.postgres import SessionFactory
from tests.domains.conftest import FakeLLMProvider
from tests.domains.test_diag_state_from_events import (  # noqa: F401
    DiagDomain,
    _auth_headers,
    _events,
    _queue_question,
    _start,
    diag_domain,
)


async def _answer_with_key(
    client: httpx.AsyncClient,
    token: str,
    domain_id: UUID,
    *,
    session_id: UUID | str,
    task_id: UUID | str,
    answer: str,
    key: str,
    confidence: float | None = 0.5,
) -> httpx.Response:
    return await client.post(
        f"/v1/domains/{domain_id}/diagnostic/answer",
        headers=_auth_headers(token),
        json={
            "session_id": str(session_id),
            "task_id": str(task_id),
            "answer": answer,
            "confidence_self_report": confidence,
            "response_time_sec": 3.0,
            "idempotency_key": key,
        },
    )


async def _start_raw(
    client: httpx.AsyncClient, token: str, domain_id: UUID, key: str
) -> httpx.Response:
    return await client.post(
        f"/v1/domains/{domain_id}/diagnostic/start",
        headers=_auth_headers(token),
        json={"idempotency_key": key},
    )


async def _seqs(db: SessionFactory, learner_id: UUID) -> list[int]:
    return [e.seq for e in await _events(db, learner_id)]


async def test_repeated_start_returns_same_session_and_calls_llm_once(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    question = _queue_question(fake_llm_provider, "s1")

    first = await _start_raw(api_client, token, diag_domain.domain_id, "start-key-1")
    assert first.status_code == 200, first.text
    seqs = await _seqs(db, learner_id)
    assert len(fake_llm_provider.calls) == 1

    second = await _start_raw(api_client, token, diag_domain.domain_id, "start-key-1")
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert second.json()["next_question"]["options"] == question.options
    assert len(fake_llm_provider.calls) == 1
    assert await _seqs(db, learner_id) == seqs


async def test_start_ids_differ_per_key(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
) -> None:
    _, token = seeded_learner
    _queue_question(fake_llm_provider, "d1")
    _queue_question(fake_llm_provider, "d2")
    a = (await _start_raw(api_client, token, diag_domain.domain_id, "key-a")).json()
    b = (await _start_raw(api_client, token, diag_domain.domain_id, "key-b")).json()
    assert a["session_id"] != b["session_id"]
    assert a["next_question"]["task_id"] != b["next_question"]["task_id"]


async def test_start_retry_after_session_advanced_returns_first_question(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "adv1")
    _queue_question(fake_llm_provider, "adv2")

    first = (await _start_raw(api_client, token, domain_id, "adv-key")).json()
    resp = await _answer_with_key(
        api_client,
        token,
        domain_id,
        session_id=first["session_id"],
        task_id=first["next_question"]["task_id"],
        answer=q1.correct,
        key="adv-answer",
    )
    assert resp.status_code == 200, resp.text
    seqs = await _seqs(db, learner_id)

    retry = await _start_raw(api_client, token, domain_id, "adv-key")
    assert retry.status_code == 200, retry.text
    assert retry.json() == first
    assert await _seqs(db, learner_id) == seqs
    assert len(fake_llm_provider.calls) == 2


async def test_start_same_key_other_domain_is_conflict(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    _queue_question(fake_llm_provider, "c1")
    first = await _start_raw(api_client, token, diag_domain.domain_id, "shared-key")
    assert first.status_code == 200, first.text
    seqs = await _seqs(db, learner_id)

    other = await _start_raw(api_client, token, uuid4(), "shared-key")
    assert other.status_code == 409, other.text
    assert other.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert len(fake_llm_provider.calls) == 1
    assert await _seqs(db, learner_id) == seqs


async def test_repeated_answer_returns_identical_body_without_new_events(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "r1")
    q2 = _queue_question(fake_llm_provider, "r2")
    started = await _start(api_client, token, domain_id)
    request: dict[str, Any] = {
        "session_id": started["session_id"],
        "task_id": started["next_question"]["task_id"],
        "answer": q1.correct,
        "key": "answer-key-1",
    }

    first = await _answer_with_key(api_client, token, domain_id, **request)
    assert first.status_code == 200, first.text
    assert first.json()["next_question"]["options"] == q2.options
    seqs = await _seqs(db, learner_id)
    calls = len(fake_llm_provider.calls)

    second = await _answer_with_key(api_client, token, domain_id, **request)
    assert second.status_code == 200, second.text
    assert second.json() == first.json()
    assert len(fake_llm_provider.calls) == calls
    assert await _seqs(db, learner_id) == seqs


async def test_answer_same_key_different_body_is_conflict(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "x1")
    _queue_question(fake_llm_provider, "x2")
    started = await _start(api_client, token, domain_id)
    base: dict[str, Any] = {
        "session_id": started["session_id"],
        "task_id": started["next_question"]["task_id"],
        "answer": q1.correct,
        "key": "x-key",
    }

    first = await _answer_with_key(api_client, token, domain_id, **base)
    assert first.status_code == 200, first.text
    seqs = await _seqs(db, learner_id)
    calls = len(fake_llm_provider.calls)

    for change in (
        {"answer": q1.options[0]},
        {"confidence": 0.9},
        {"task_id": uuid4()},
        {"session_id": uuid4()},
    ):
        resp = await _answer_with_key(api_client, token, domain_id, **{**base, **change})
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _seqs(db, learner_id) == seqs
    assert len(fake_llm_provider.calls) == calls


async def test_retry_of_first_answer_after_second_answer_returns_first_response(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    db: SessionFactory,
) -> None:
    learner_id, token = seeded_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "t1")
    q2 = _queue_question(fake_llm_provider, "t2")
    _queue_question(fake_llm_provider, "t3")
    started = await _start(api_client, token, domain_id)
    first_request: dict[str, Any] = {
        "session_id": started["session_id"],
        "task_id": started["next_question"]["task_id"],
        "answer": q1.correct,
        "key": "turn-1",
    }

    first = await _answer_with_key(api_client, token, domain_id, **first_request)
    assert first.status_code == 200, first.text
    second_task = first.json()["next_question"]
    assert second_task["options"] == q2.options
    second = await _answer_with_key(
        api_client,
        token,
        domain_id,
        session_id=started["session_id"],
        task_id=second_task["task_id"],
        answer=q2.options[0],
        key="turn-2",
    )
    assert second.status_code == 200, second.text
    assert second.json() != first.json()
    seqs = await _seqs(db, learner_id)
    calls = len(fake_llm_provider.calls)

    retry = await _answer_with_key(api_client, token, domain_id, **first_request)
    assert retry.status_code == 200, retry.text
    assert retry.json() == first.json()
    assert await _seqs(db, learner_id) == seqs
    assert len(fake_llm_provider.calls) == calls
