"""Topic cards (ТЗ Ф4 WP23.4; Т-§17.5, П-11.2; D-C-140).

A card is a task with markup (Т-§17.5): every card is a task of the bank (`content.tasks` and
`task.created`, `quack.practice.task_store`), `card_id` is its `task_id`. So an answer to a card
is the ordinary `answer.submitted {task_id, source: card}` (the check of B, the projector row of
A, D-A-125 п.1), and the cards artifact keeps only what the learner sees, versioned as every
artifact (`quack.artifacts.documents`).

No schedule (Т-§14.3): the skill is repeated, not the card. At most `cards_per_topic_max` per
topic. Editing and deleting are one touch and a new version (Т-§17.5): a task of the bank never
changes (Т-§5.1), so an edited card is a new task with the same markup, family and source, and the
card in the artifact points to it; a deleted card leaves the next version, its task stays in the
bank as every shown task does.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid5

from psycopg.types.json import Jsonb
from redis.asyncio import Redis

from quack.artifacts.documents import (
    Artifact,
    ArtifactNotFound,
    Card,
    _domain_of_owner,
    _ensure_artifact,
    _save,
    _save_learner,
    artifact_id_for,
    get_artifact,
)
from quack.artifacts.topic_export import answer_text
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import TaskCreated
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task, get_task

_ARTIFACT_OF_CARD = (
    "SELECT a.artifact_id FROM content.artifacts a JOIN content.artifact_versions v "
    "ON v.artifact_id = a.artifact_id AND v.version = a.current_version "
    "AND v.learner_id = %(learner_id)s "
    "WHERE a.learner_id = %(learner_id)s AND a.kind = 'cards' AND v.content -> 'cards' @> %(probe)s"
)
# A repeated request finds its artifact by the key: the card may have left the current version.
_ARTIFACT_OF_KEY = (
    "SELECT payload ->> 'artifact_id' AS artifact_id FROM core.events "
    "WHERE learner_id = %(learner_id)s AND idempotency_key = %(key)s"
)
_TASK_CREATED = (
    "SELECT payload, domain_id FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'task.created' AND payload ->> 'task_id' = %(task_id)s ORDER BY seq LIMIT 1"
)


async def topic_cards(db: Db, *, learner_id: UUID, topic_id: UUID) -> Artifact:
    """`GET /v1/topics/{id}/cards`: the current version; `ArtifactNotFound` — not the learner's
    topic."""
    return await get_artifact(db, learner_id=learner_id, kind="cards", owner_id=topic_id)


async def card_answers(
    db: Db, *, learner_id: UUID, cards: Sequence[Card]
) -> dict[UUID, str | None]:
    """The reference answer of each card, for the learner to reveal (the back of the card, the
    same text as in the Anki export): read from the answer spec of the task behind it, for this
    learner only (Т-§3.5). A task with no short answer, or a card with no task, gives `None`."""
    out: dict[UUID, str | None] = {}
    for card in cards:
        task = await get_task(db, learner_id=learner_id, task_id=card.card_id)
        out[card.card_id] = (answer_text(task.answer_spec) or None) if task is not None else None
    return out


async def write_system_cards(
    db: Db,
    settings: Settings,
    *,
    learner_id: UUID,
    topic_id: UUID,
    cards: Sequence[Card],
    created_by: str,
    skill_version: int | None = None,
    redis: Redis | None = None,
) -> Artifact:
    """New cards of the writer (WP23.2) after the current ones; their tasks are already in the
    bank. A card already there is not added again (law 5); cards beyond `cards_per_topic_max`
    are not written (Т-§17.5)."""
    domain_id = await _domain_of_owner(db, learner_id, "cards", topic_id)
    artifact_id = artifact_id_for(learner_id, "cards", topic_id)

    def change(current: list[Card]) -> list[Card]:
        known = {card.card_id for card in current}
        new = [card for card in cards if card.card_id not in known]
        room = max(settings.cards_per_topic_max - len(current), 0)
        return [*current, *new[:room]]

    await _ensure_artifact(db, learner_id, "cards", topic_id, domain_id)
    return await _save(
        db,
        learner_id=learner_id,
        artifact_id=artifact_id,
        change=change,
        created_by=created_by,
        actor=created_by,
        skill_version=skill_version,
        redis=redis,
    )


async def edit_card(
    db: Db,
    *,
    learner_id: UUID,
    card_id: UUID,
    prompt: str,
    idempotency_key: str,
    redis: Redis | None = None,
) -> Artifact:
    """`PATCH /v1/cards/{id}`: the learner rewrites what the card asks. The task behind it gets a
    successor with the new condition and the same markup (Т-§5.1: a task does not change), the
    card points to it and becomes `owner = learner`. `ArtifactNotFound` — no such card."""
    artifact_id = await _artifact_of_card(db, learner_id, card_id, idempotency_key)
    created = await db.fetch_one(_TASK_CREATED, {"learner_id": learner_id, "task_id": str(card_id)})
    document = await get_task(db, learner_id=learner_id, task_id=card_id)
    if created is None or document is None:
        raise ArtifactNotFound(f"card {card_id} not found")
    task = TaskCreated.model_validate(created["payload"])
    successor = uuid5(card_id, f"card.edit:{idempotency_key}")

    def change(current: list[Card]) -> list[Card]:
        for index, card in enumerate(current):
            if card.card_id == card_id:
                edited = card.model_copy(
                    update={"card_id": successor, "prompt": prompt, "owner": "learner"}
                )
                return [*current[:index], edited, *current[index + 1 :]]
        raise ArtifactNotFound(f"card {card_id} not found")

    async with db.transaction():
        await create_task(
            db,
            learner_id=learner_id,
            task_id=successor,
            family_id=task.family_id,
            source=task.source,
            condition=TaskCondition(text=prompt, latex=None),
            markup=task.markup,
            verification=task.verification,
            actor="learner",
            idempotency_key=f"card.edit:{idempotency_key}:task",
            domain_id=created["domain_id"],
        )
        return await _save_learner(db, learner_id, artifact_id, change, idempotency_key, redis)


async def delete_card(
    db: Db,
    *,
    learner_id: UUID,
    card_id: UUID,
    idempotency_key: str,
    redis: Redis | None = None,
) -> Artifact:
    """`DELETE /v1/cards/{id}`: the next version without the card. `ArtifactNotFound`."""
    artifact_id = await _artifact_of_card(db, learner_id, card_id, idempotency_key)

    def change(current: list[Card]) -> list[Card]:
        if all(card.card_id != card_id for card in current):
            raise ArtifactNotFound(f"card {card_id} not found")
        return [card for card in current if card.card_id != card_id]

    return await _save_learner(db, learner_id, artifact_id, change, idempotency_key, redis)


async def _artifact_of_card(db: Db, learner_id: UUID, card_id: UUID, idempotency_key: str) -> UUID:
    key = f"artifact.edit:{idempotency_key}"
    done = await db.fetch_one(_ARTIFACT_OF_KEY, {"learner_id": learner_id, "key": key})
    if done is not None and done["artifact_id"] is not None:
        return UUID(done["artifact_id"])
    probe: list[dict[str, Any]] = [{"card_id": str(card_id)}]
    row = await db.fetch_one(_ARTIFACT_OF_CARD, {"learner_id": learner_id, "probe": Jsonb(probe)})
    if row is None:
        raise ArtifactNotFound(f"card {card_id} not found")
    return row["artifact_id"]
