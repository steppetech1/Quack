"""The name of a session (owner decision 2026-11-19): the provisional title from the first words
of the learner (cut at a word boundary, one line, voice too), the old session without a title,
the job `session.title` (one event under `session.title:{session_id}`, one transaction with
`content.sessions.title`, a repeat does not overwrite), the routes `GET /v1/sessions` and
`GET /v1/sessions/{id}`."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from uuid import UUID, uuid4

import httpx
import pytest
from pydantic import BaseModel

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.llm.gateway import LLMGateway, LLMResult
from quack.practice.role_outputs import SessionTitleOutput
from quack.prompts.registry import PromptRegistry
from quack.session.sessions import (
    StartSession,
    active_session,
    get_session,
    list_sessions,
    provisional_title,
    start_session,
)
from quack.session.titles import NONE_LINE, clean_title, title_key, title_session
from tests.db.fixtures import create_learner
from tests.events.samples import sample_payload
from tests.session.helpers import Learner
from tests.session.test_sessions_db import _domain

pytest_plugins = ["tests.session.fixtures"]

SETTINGS = Settings()
CHARS = SETTINGS.session_title_provisional_chars


@dataclass
class _Gateway:
    title: str = "Limits of sequences"
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append(
            {
                "role": role,
                "version": prompt_version,
                "variables": variables,
                "key": idempotency_key,
            }
        )
        return LLMResult(
            value=SessionTitleOutput(title=self.title),
            from_cache=False,
            prompt_hash="h",
            model="m",
            usage=None,
        )


async def _say(
    db: Db, learner_id: UUID, session_id: UUID, text: str, type_: str = "message.sent"
) -> None:
    payload: dict[str, Any] = {"text": text}
    if type_ == "voice.transcribed":
        payload = {"text": text, "started_at_ms": 0, "ended_at_ms": 900}
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type=type_,
                payload=payload,
                actor="learner",
                idempotency_key=f"{type_}:{uuid4()}",
                session_id=session_id,
            )
        ],
    )


async def _reply(db: Db, learner_id: UUID, session_id: UUID, text: str) -> int:
    payload = {**sample_payload("turn.committed"), "text": text}
    (stored,) = await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload=payload,
                actor="teacher@3",
                idempotency_key=f"turn:{uuid4()}",
                session_id=session_id,
            )
        ],
    )
    return stored.seq


async def _session(db: Db, intent: str = "free") -> tuple[UUID, UUID]:
    learner_id = await create_learner(db)
    domain_id = await _domain(db, learner_id)
    body = StartSession.model_validate(
        {"domain_id": domain_id, "intent": intent, "idempotency_key": uuid4()}
    )
    started = await start_session(db, learner_id=learner_id, body=body)
    return learner_id, started.session_id


async def _column(db: Db, session_id: UUID) -> str | None:
    row = await db.fetch_one(
        "SELECT title FROM content.sessions WHERE session_id = %(s)s", {"s": session_id}
    )
    assert row is not None
    return cast(str | None, row["title"])


async def _titled(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT seq, actor, idempotency_key, session_id, causation_seq, payload FROM core.events "
        "WHERE learner_id = %(l)s AND type = 'session.titled' ORDER BY seq",
        {"l": learner_id},
    )


# --- the provisional title -----------------------------------------------------------------


def test_provisional_title_cuts_at_a_word_boundary_on_one_line() -> None:
    assert provisional_title("Why does the limit\nof 1/n  go to zero?", 100) == (
        "Why does the limit of 1/n go to zero?"
    )
    cut = provisional_title("Explain how to take the derivative of a composite function", 40)
    assert cut == "Explain how to take the derivative of a…"
    # a word that ends exactly on the limit stays whole
    assert provisional_title("aaaa bbbb cccc", 9) == "aaaa bbbb…"
    # one long word with no spaces is cut hard
    assert provisional_title("x" * 50, 10) == "x" * 10 + "…"
    assert provisional_title("short", 40) == "short"


@pytest.mark.parametrize("text", [None, "", "   \n\t "])
def test_provisional_title_of_nothing_is_none(text: str | None) -> None:
    assert provisional_title(text, CHARS) is None


async def test_session_without_a_title_shows_the_first_words(pg_db: Db) -> None:
    """An old session (the column is empty): the title is computed on read from the first words
    of the learner — typed or transcribed, not the empty text of a voice message; before the
    first word there is none, and the client names the session by its intent and topic."""
    learner_id, session_id = await _session(pg_db)
    assert (await get_session(pg_db, learner_id=learner_id, session_id=session_id)).title is None

    await _say(pg_db, learner_id, session_id, "")  # the voice message itself carries no text
    await _say(pg_db, learner_id, session_id, "minus b over 2a", "voice.transcribed")
    await _say(pg_db, learner_id, session_id, "and the second question")
    view = await get_session(pg_db, learner_id=learner_id, session_id=session_id)
    assert view.title == "minus b over 2a"
    assert await _column(pg_db, session_id) is None  # nothing is renamed in bulk

    other_id, other_session = await _session(pg_db)
    await _say(pg_db, other_id, other_session, "Please explain the chain rule in detail " * 3)
    [listed] = await list_sessions(pg_db, learner_id=other_id)
    assert listed.title is not None and listed.title.endswith("…")
    assert len(listed.title) <= CHARS + 1
    active = await active_session(pg_db, learner_id=other_id)
    assert active is not None and active.title == listed.title


async def test_stored_title_wins_over_the_first_words(pg_db: Db) -> None:
    learner_id, session_id = await _session(pg_db)
    await _say(pg_db, learner_id, session_id, "hello there")
    await pg_db.execute(
        "UPDATE content.sessions SET title = 'Quadratic equations' WHERE session_id = %(s)s",
        {"s": session_id},
    )
    view = await get_session(pg_db, learner_id=learner_id, session_id=session_id)
    assert view.title == "Quadratic equations"
    [listed] = await list_sessions(pg_db, learner_id=learner_id)
    assert listed.title == "Quadratic equations"


# --- the job -------------------------------------------------------------------------------


def test_clean_title_drops_quotes_the_final_full_stop_and_extra_lines() -> None:
    assert clean_title('  "Limits of sequences."  ', 60) == "Limits of sequences"
    assert clean_title("«Пределы последовательностей»\nпояснение", 60) == (
        "Пределы последовательностей"
    )
    assert clean_title("one two three four five", 12) == "one two"
    assert clean_title(' "". ', 60) == ""


async def test_job_names_the_session_once_in_one_transaction(pg_db: Db) -> None:
    learner_id, session_id = await _session(pg_db)
    await _say(pg_db, learner_id, session_id, "What is the limit of 1/n?")
    await _say(pg_db, learner_id, session_id, "and why is it zero")
    trigger = await _reply(pg_db, learner_id, session_id, "It tends to zero.")
    gateway = _Gateway(title=' "Limits of sequences." ')

    async def run(g: _Gateway) -> str | None:
        return await title_session(
            pg_db,
            cast(LLMGateway, g),
            SETTINGS,
            learner_id=learner_id,
            session_id=session_id,
            trigger_seq=trigger,
        )

    assert await run(gateway) == "Limits of sequences"
    [call] = gateway.calls
    assert (call["role"], call["version"], call["key"]) == (
        "session_titler",
        1,
        f"session.title:{session_id}",
    )
    assert call["variables"]["messages"].splitlines() == [
        "1. What is the limit of 1/n?",
        "2. and why is it zero",
    ]
    assert call["variables"]["reply"] == "It tends to zero."
    PromptRegistry().load("session_titler", 1).render(call["variables"])

    [event] = await _titled(pg_db, learner_id)
    assert event["idempotency_key"] == title_key(session_id) == f"session.title:{session_id}"
    assert event["payload"] == {"session_id": str(session_id), "title": "Limits of sequences"}
    assert (event["actor"], event["session_id"], event["causation_seq"]) == (
        "session_titler@1",
        session_id,
        trigger,
    )
    assert await _column(pg_db, session_id) == "Limits of sequences"
    view = await get_session(pg_db, learner_id=learner_id, session_id=session_id)
    assert view.title == "Limits of sequences"

    # a repeat of the job, or a job of a later turn: no model call, no second event, no overwrite
    again = _Gateway(title="Something else entirely")
    assert await run(again) is None
    assert again.calls == []
    assert len(await _titled(pg_db, learner_id)) == 1
    assert await _column(pg_db, session_id) == "Limits of sequences"


async def test_job_uses_the_first_reply_when_the_learner_pressed_a_button(pg_db: Db) -> None:
    """A session started from an intent button has no typed message: the first reply of the
    teacher names it; a session where nobody has spoken is left alone, without a call."""
    learner_id, session_id = await _session(pg_db, "practice")
    gateway = _Gateway()
    kwargs: dict[str, Any] = {"learner_id": learner_id, "session_id": session_id}
    assert await title_session(pg_db, cast(LLMGateway, gateway), SETTINGS, **kwargs) is None
    assert gateway.calls == []

    await _reply(pg_db, learner_id, session_id, "Here is a task on limits.")
    assert await title_session(pg_db, cast(LLMGateway, gateway), SETTINGS, **kwargs) is not None
    [call] = gateway.calls
    assert call["variables"]["messages"] == NONE_LINE
    assert call["variables"]["reply"] == "Here is a task on limits."


async def test_job_with_an_empty_answer_commits_nothing(pg_db: Db) -> None:
    """Law 7: the model gave nothing usable — the job fails, no event, no column."""
    learner_id, session_id = await _session(pg_db)
    await _say(pg_db, learner_id, session_id, "hello")
    with pytest.raises(ValueError, match="empty title"):
        await title_session(
            pg_db,
            cast(LLMGateway, _Gateway(title=' "." ')),
            SETTINGS,
            learner_id=learner_id,
            session_id=session_id,
        )
    assert await _titled(pg_db, learner_id) == []
    assert await _column(pg_db, session_id) is None


async def test_job_of_a_session_that_is_gone_does_nothing(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    gateway = _Gateway()
    named = await title_session(
        pg_db, cast(LLMGateway, gateway), SETTINGS, learner_id=learner_id, session_id=uuid4()
    )
    assert named is None
    assert gateway.calls == []


# --- the routes ----------------------------------------------------------------------------


async def test_routes_return_the_title(
    client: httpx.AsyncClient, learner: Learner, pg_db: Db
) -> None:
    """The list and the detail give `title`: the first words of an untitled session (old ones
    too), `null` before the first word, the stored name once the job wrote it."""
    response = await client.post(
        "/v1/sessions",
        json={
            "domain_id": str(learner.domain_id),
            "intent": "learn",
            "idempotency_key": str(uuid4()),
        },
        cookies=learner.cookies,
    )
    assert response.status_code == 201, response.text
    assert response.json()["title"] is None
    session_id = UUID(response.json()["session_id"])

    await _say(pg_db, learner.learner_id, session_id, "How do I factor x^2 - 5x + 6?")
    listed = await client.get("/v1/sessions", cookies=learner.cookies)
    assert [s["title"] for s in listed.json()["sessions"]] == ["How do I factor x^2 - 5x + 6?"]
    detail = await client.get(f"/v1/sessions/{session_id}", cookies=learner.cookies)
    assert detail.json()["title"] == "How do I factor x^2 - 5x + 6?"

    await title_session(
        pg_db,
        cast(LLMGateway, _Gateway(title="Factoring quadratics")),
        SETTINGS,
        learner_id=learner.learner_id,
        session_id=session_id,
    )
    listed = await client.get("/v1/sessions", cookies=learner.cookies)
    assert [s["title"] for s in listed.json()["sessions"]] == ["Factoring quadratics"]
    detail = await client.get(f"/v1/sessions/{session_id}", cookies=learner.cookies)
    assert detail.json()["title"] == "Factoring quadratics"
    assert any(e["type"] == "session.titled" for e in detail.json()["events"])
    active = await client.get("/v1/sessions/active", cookies=learner.cookies)
    assert active.json()["title"] == "Factoring quadratics"
