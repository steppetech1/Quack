"""E2E: the failure table (ТЗ Ф4 §8.14, T-E11; Т-§20.7, §4.4).

One row per dependency: the stand runs with that dependency broken and the behaviour of the
table is checked. A turn of the teacher survives a failure without a mark where the table says
so; the marks of §4.4 are `llm_unavailable` and `memory_unavailable`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest

from tests.e2e.conftest import StandFactory
from tests.e2e.journey import ok, onboard
from tests.e2e.system import System
from tests.e2e.text_journey import act, run_text_journey, script_teacher, start_session
from tests.session.helpers import parse_sse
from tests.support.broken import BrokenNeo4jDriver, BrokenRedis
from tests.support.deletions import FakeDeletionJournal
from tests.support.email import FakeEmailSender
from tests.support.embeddings import FakeEmbeddingClient
from tests.support.llm import ScriptedFailure
from tests.support.storage import BrokenObjectStore

ALWAYS = 1_000_000
PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n"


async def turn_mark(system: System) -> str | None:
    """A message to the teacher on a topic; the `degraded_mode` of the committed turn."""
    script_teacher(system, ())
    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, f"te11:{uuid4()}")
    session_id = await start_session(system, learner, domain_id, topic_id)
    response = await act(system, learner, session_id, {"kind": "message", "text": "Hello"})
    assert response.status_code == 200, response.text
    frames = parse_sse(response.content)
    names = [n for n, _ in frames]
    assert names[0] == "action_recorded" and names[-1] == "turn_committed", frames
    assert "error" not in names, frames
    return frames[-1][1]["degraded_mode"]


async def meta_is_down(system: System) -> None:
    system.llm.fail(ScriptedFailure("unavailable", times=ALWAYS))
    assert await turn_mark(system) == "llm_unavailable"


async def neo4j_is_down(system: System) -> None:
    assert await turn_mark(system) == "memory_unavailable"


async def redis_is_down(system: System) -> None:
    """The lock of the turn falls back to Postgres; no mark."""
    assert await turn_mark(system) is None


async def modal_is_down(system: System) -> None:
    """The embeddings are down: the turn works from the graph, no mark."""
    assert await turn_mark(system) is None


async def storage_is_down(system: System) -> None:
    """Uploads answer `SERVICE_UNAVAILABLE`; the turn works from Postgres."""
    learner = await system.sign_in_by_link()
    domain_id, _ = await onboard(system, learner, f"te11:{uuid4()}")
    upload = await system.client.post(
        f"/v1/domains/{domain_id}/materials",
        files={"file": ("notes.pdf", PDF, "application/octet-stream")},
        headers=learner.headers,
    )
    assert upload.status_code == 503, upload.text
    assert upload.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert upload.json()["error"]["message"]
    assert await turn_mark(system) is None


async def compute_is_down(system: System) -> None:
    """The scripted compute answers 503: the instant check still gives its verdict."""
    journey = await run_text_journey(system)
    verdict = journey.answer_frames[0][1]["verdict"]
    assert verdict["outcome"] == "correct", journey.answer_frames


async def r2_is_down(system: System) -> None:
    """The journal of deletions is down: the deletion does not run."""
    learner = await system.sign_in_by_link()
    _, topic_id = await onboard(system, learner, f"te11:{uuid4()}")
    refused = await system.client.delete(f"/v1/topics/{topic_id}", headers=learner.headers)
    assert refused.status_code == 503, refused.text
    assert refused.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
    card = ok(await system.client.get(f"/v1/topics/{topic_id}", headers=learner.headers))
    assert card["topic_id"] == str(topic_id)


async def resend_is_down(system: System) -> None:
    """A login link cannot be sent; the API says so (Google sign-in is not part of the stand)."""
    sent = await system.client.post("/auth/link", json={"email": "someone@example.com"})
    assert sent.status_code == 503, sent.text
    assert sent.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


type Row = tuple[Callable[[], dict[str, Any]], Callable[[System], Awaitable[None]]]

ROWS: dict[str, Row] = {
    "meta": (lambda: {}, meta_is_down),
    "neo4j": (lambda: {"neo4j_driver": BrokenNeo4jDriver()}, neo4j_is_down),
    "redis": (lambda: {"redis_client": BrokenRedis()}, redis_is_down),
    "modal": (lambda: {"embeddings": FakeEmbeddingClient(8, available=False)}, modal_is_down),
    "storage": (lambda: {"storage": BrokenObjectStore()}, storage_is_down),
    "compute": (lambda: {}, compute_is_down),
    "r2": (lambda: {"deletions": FakeDeletionJournal(available=False)}, r2_is_down),
    "resend": (lambda: {"email": FakeEmailSender(available=False)}, resend_is_down),
}


# Rows that are red on the code of today (D-C-191): needed from stream B / A, not changed here.
RED = {
    "meta": "B: turn_source sets llm_unavailable=False; a down provider ends the turn with an "
    "`error` frame LLM_DEGRADED instead of a turn marked llm_unavailable (ТЗ §4.4)",
    "neo4j": "A/B: ServiceUnavailable of Neo4j escapes the assembly of the turn (`turn_failed`) "
    "instead of the mark memory_unavailable",
}


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(n, marks=pytest.mark.xfail(strict=True, reason=RED[n])) if n in RED else n
        for n in ROWS
    ],
)
async def test_failure_modes_t207(stand: StandFactory, name: str) -> None:
    """T-E11."""
    resources, behaviour = ROWS[name]
    async with stand(**resources()) as system:
        await behaviour(system)
