"""Diagnostic writes publish `projector:wakeup` after commit (T-019 D-4)."""

# ruff: noqa: F811

from __future__ import annotations

import asyncio
from uuid import UUID

import httpx
from redis.asyncio import Redis

from quack.db.redis import PROJECTOR_WAKEUP_CHANNEL
from tests.domains.conftest import FakeLLMProvider
from tests.domains.test_diag_state_from_events import (  # noqa: F401
    DiagDomain,
    _answer,
    _finish,
    _queue_question,
    _start,
    diag_domain,
)


async def _wait_wakeup(pubsub: object) -> bool:
    for _ in range(20):
        message = await pubsub.get_message(  # type: ignore[attr-defined]
            ignore_subscribe_messages=True, timeout=0.25
        )
        if message:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_start_answer_finish_publish_wakeup(
    api_client: httpx.AsyncClient,
    seeded_learner: tuple[UUID, str],
    diag_domain: DiagDomain,
    fake_llm_provider: FakeLLMProvider,
    redis: Redis,
) -> None:
    _, token = seeded_learner
    domain_id = diag_domain.domain_id
    q1 = _queue_question(fake_llm_provider, "q1")
    _queue_question(fake_llm_provider, "q2")

    pubsub = redis.pubsub()
    await pubsub.subscribe(PROJECTOR_WAKEUP_CHANNEL)
    await pubsub.get_message(timeout=1)  # subscribe confirmation
    try:
        started = await _start(api_client, token, domain_id)
        assert await _wait_wakeup(pubsub), "no wakeup after start"

        first = started["next_question"]
        resp = await _answer(
            api_client, token, domain_id, started["session_id"], first["task_id"], q1.correct
        )
        assert resp.status_code == 200, resp.text
        assert await _wait_wakeup(pubsub), "no wakeup after answer"

        finished = await _finish(api_client, token, domain_id, started["session_id"])
        assert finished.status_code == 200, finished.text
        assert await _wait_wakeup(pubsub), "no wakeup after finish"
    finally:
        await pubsub.aclose()  # type: ignore[no-untyped-call]
