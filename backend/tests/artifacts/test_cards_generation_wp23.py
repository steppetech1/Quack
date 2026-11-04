"""WP23.4: topic cards written by `artifact_writer@1` — each a task of the bank with markup, at most
`cards_per_topic_max` on a topic (ТЗ Ф4 WP23.4, WP23.2; Т-§17.5, Т-§5.3; D-C-140, D-C-154)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from quack.artifacts.cards import topic_cards
from quack.artifacts.topic_refresh import refresh_topic, refresh_topic_cards
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.practice.task_store import get_task
from tests.artifacts.test_topic_refresh_wp23 import DOMAIN, LIMIT, TopicReader, _prefetch
from tests.db.fixtures import create_learner
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

WRITER = "artifact_writer@1"


def _card(prompt: str, skill: str = "S1", kind: str = "sign_to_method") -> dict[str, Any]:
    return {
        "kind": kind,
        "prompt": prompt,
        "markup": {
            "tests": [{"skill": skill, "level": 3}],
            "methodNamed": False,
            "answer": {"type": "number", "value": {"latex": "\\frac{1}{3}", "sympy": "1/3"}},
        },
    }


async def _topic(db: Db, learner: UUID) -> UUID:
    topic_id = uuid4()
    created = NewEvent(
        type="topic.created",
        payload={"topic_id": topic_id, "domain_id": DOMAIN, "name": "Limits", "origin": "session"},
        actor="code",
        idempotency_key=f"topic:{topic_id}",
        domain_id=DOMAIN,
    )
    await append_events(db, learner, [created])
    return topic_id


async def _task_events(db: Db, learner: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT payload, domain_id, actor FROM core.events "
        "WHERE learner_id = %(learner_id)s AND type = 'task.created' ORDER BY seq",
        {"learner_id": learner},
    )


def _writer(provider: ScriptedLLMProvider, cards: list[dict[str, Any]]) -> None:
    provider.respond("ArtifactWriterOutput", lambda _r: {"blocks": [], "cards": cards})


async def test_cards_are_tasks_of_the_bank_with_markup(pg_db: Db, settings: Settings) -> None:
    """Т-§17.5, Т-§17.3: every card is a task with markup in the bank (`task.created`, skill label
    turned into an id); an unknown label is a violation — one more generation, then the card is
    dropped; the writer is asked for cards; a repeated job writes no new version and no task."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    provider = ScriptedLLMProvider()
    _writer(provider, [_card("When the ratio test?"), _card("Which skill?", skill="S9")])
    resources = build_app_resources(settings, None, None, db=pg_db, provider=provider)
    reader = TopicReader(topic_id, 1)

    version = await refresh_topic_cards(
        resources.db, resources.llm, reader, _prefetch, settings,
        learner_id=learner, topic_id=topic_id, key="job-1",
    )  # fmt: skip

    assert version == 1
    calls = provider.calls_for(schema_name="ArtifactWriterOutput")
    assert len(calls) == 2 and "### Artifact type\n\ncards" in calls[0].prompt
    cards = await topic_cards(pg_db, learner_id=learner, topic_id=topic_id)
    (card,) = cards.cards
    assert (card.kind, card.owner) == ("sign_to_method", "system")
    assert card.prompt == "When the ratio test?"
    task = await get_task(pg_db, learner_id=learner, task_id=card.card_id)
    assert task is not None and task.condition.text == "When the ratio test?"
    assert task.source == "generated" and task.domain_id == DOMAIN
    (created,) = await _task_events(pg_db, learner)
    assert created["actor"] == WRITER and created["payload"]["verification"] == "unchecked"
    assert created["payload"]["task_id"] == str(card.card_id)
    assert created["payload"]["family_id"] == str(card.card_id)
    (tested,) = created["payload"]["markup"]["tests"]
    assert (tested["skill_id"], tested["level"]) == (str(LIMIT), 3)

    again = await refresh_topic_cards(
        resources.db, resources.llm, reader, _prefetch, settings,
        learner_id=learner, topic_id=topic_id, key="job-1",
    )  # fmt: skip
    assert again == 1
    assert len(await _task_events(pg_db, learner)) == 1


async def test_cards_per_topic_max_stops_the_writing(pg_db: Db, settings: Settings) -> None:
    """Т-§17.5: no more than `cards_per_topic_max` on a topic — cards beyond it get no task and no
    place; a topic that is full is not sent to the model at all."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    capped = settings.model_copy(update={"cards_per_topic_max": 2})
    provider = ScriptedLLMProvider()
    _writer(provider, [_card(f"Card {i}", skill="S2") for i in range(3)])
    resources = build_app_resources(capped, None, None, db=pg_db, provider=provider)
    reader = TopicReader(topic_id, 1)

    version = await refresh_topic_cards(
        resources.db, resources.llm, reader, _prefetch, capped,
        learner_id=learner, topic_id=topic_id, key="job-1",
    )  # fmt: skip
    assert version == 1
    cards = await topic_cards(pg_db, learner_id=learner, topic_id=topic_id)
    assert [c.prompt for c in cards.cards] == ["Card 0", "Card 1"]
    assert len(await _task_events(pg_db, learner)) == 2
    assert "at most 2 new" in provider.calls_for(schema_name="ArtifactWriterOutput")[0].prompt

    before = len(provider.calls)
    full = await refresh_topic_cards(
        resources.db, resources.llm, reader, _prefetch, capped,
        learner_id=learner, topic_id=topic_id, key="job-2",
    )  # fmt: skip
    assert full is None and len(provider.calls) == before
    assert await get_task(pg_db, learner_id=learner, task_id=cards.cards[1].card_id) is not None


async def test_one_job_refreshes_notes_and_cards(pg_db: Db, settings: Settings) -> None:
    """Т-§17.7: after a session the topic gets new notes and new cards (`artifact:{topic}`); an
    unknown topic gets nothing."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    block = {"updates": None, "kind": "summary", "content": "Limits…", "sources": [], "skills": []}
    provider = ScriptedLLMProvider()
    provider.respond(
        "ArtifactWriterOutput", lambda _r: {"blocks": [block], "cards": [_card("Recall?")]}
    )
    resources = build_app_resources(settings, None, None, db=pg_db, provider=provider)

    notes, cards = await refresh_topic(
        resources.db, resources.llm, TopicReader(topic_id, 1), _prefetch, settings,
        learner_id=learner, topic_id=topic_id, key="job-1",
    )  # fmt: skip
    assert (notes, cards) == (1, 1)
    unknown = await refresh_topic(
        resources.db, resources.llm, TopicReader(uuid4(), 1), _prefetch, settings,
        learner_id=learner, topic_id=topic_id, key="job-2",
    )  # fmt: skip
    assert unknown == (None, None)
