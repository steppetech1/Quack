"""WP23.4: topic cards — each a task of the bank, versioned, edited and deleted in one touch
(ТЗ Ф4 WP23.4; Т-§17.5, Т-§5.1; П-11.2; D-C-140)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.ratelimit import enforce_action_rate_limit
from quack.artifacts.cards import delete_card, edit_card, topic_cards, write_system_cards
from quack.artifacts.documents import ArtifactNotFound, Card
from quack.config import Settings
from quack.db import Db
from quack.events import records
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerSpec, MathValue
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task, get_task
from tests.db.fixtures import create_learner
from tests.support.auth import session_cookie, session_token
from tests.support.resources import build_app_resources

WRITER = "artifact_writer@1"
MARKUP = records.TaskMarkupRecord(
    tests=[records.TestedLevelRecord(skill_id=uuid4(), level=3)],
    method_named=False,
    answer=AnswerSpec(type="number", value=MathValue(latex="\\frac{1}{3}", sympy="1/3")),
)


async def _topic(db: Db, learner_id: UUID) -> UUID:
    topic_id, domain_id = uuid4(), uuid4()
    created = NewEvent(
        type="topic.created",
        payload={
            "topic_id": topic_id, "domain_id": domain_id, "name": "Limits", "origin": "session"
        },
        actor="code",
        idempotency_key=f"topic:{topic_id}",
        domain_id=domain_id,
    )  # fmt: skip
    await append_events(db, learner_id, [created])
    return topic_id


async def _card(db: Db, learner_id: UUID, prompt: str) -> Card:
    """A card as the writer leaves it: its task already in the bank."""
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=task_id,
        source="generated",
        condition=TaskCondition(text=prompt, latex=None),
        markup=MARKUP,
        verification="unchecked",
        actor=WRITER,
        idempotency_key=f"card:{task_id}",
    )
    return Card(card_id=task_id, kind="sign_to_method", prompt=prompt, owner="system")


async def test_cards_are_capped_and_not_repeated(pg_db: Db, settings: Settings) -> None:
    """Т-§17.5: no more than `cards_per_topic_max` on a topic; a card already there is not added
    again, the same cards again write no version (law 5)."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    capped = settings.model_copy(update={"cards_per_topic_max": 2})
    first, second, third = [await _card(pg_db, learner, f"card {i}") for i in range(3)]
    written = await write_system_cards(
        pg_db, capped, learner_id=learner, topic_id=topic_id, cards=[first, second, third],
        created_by=WRITER,
    )  # fmt: skip
    assert written.version == 1
    assert [c.card_id for c in written.cards] == [first.card_id, second.card_id]
    again = await write_system_cards(
        pg_db, capped, learner_id=learner, topic_id=topic_id, cards=[first], created_by=WRITER
    )
    assert again.version == 1
    read = await topic_cards(pg_db, learner_id=learner, topic_id=topic_id)
    assert read.cards == written.cards and read.kind == "cards"


async def test_edit_is_a_new_task_and_delete_a_new_version(pg_db: Db, settings: Settings) -> None:
    """Т-§17.5, Т-§5.1: an edited card points to a successor task with the new condition and the
    same markup; the old task stays; a repeat of the request writes nothing; delete — the next
    version without the card; another learner's card is not found."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    card = await _card(pg_db, learner, "Ratio test when?")
    await write_system_cards(
        pg_db, settings, learner_id=learner, topic_id=topic_id, cards=[card], created_by=WRITER
    )
    edited = await edit_card(
        pg_db, learner_id=learner, card_id=card.card_id, prompt="When the ratio test?",
        idempotency_key="e1",
    )  # fmt: skip
    (new,) = edited.cards
    assert edited.version == 2 and new.owner == "learner" and new.kind == card.kind
    assert new.card_id != card.card_id and new.prompt == "When the ratio test?"
    successor = await get_task(pg_db, learner_id=learner, task_id=new.card_id)
    assert successor is not None and successor.condition.text == "When the ratio test?"
    assert successor.answer_spec == MARKUP.answer
    assert await get_task(pg_db, learner_id=learner, task_id=card.card_id) is not None
    repeat = await edit_card(
        pg_db, learner_id=learner, card_id=card.card_id, prompt="When the ratio test?",
        idempotency_key="e1",
    )  # fmt: skip
    assert repeat.version == 2 and repeat.cards == edited.cards

    with pytest.raises(ArtifactNotFound):
        await delete_card(pg_db, learner_id=other, card_id=new.card_id, idempotency_key="d0")
    deleted = await delete_card(
        pg_db, learner_id=learner, card_id=new.card_id, idempotency_key="d1"
    )
    assert (deleted.version, deleted.cards) == (3, ())
    with pytest.raises(ArtifactNotFound):
        await delete_card(pg_db, learner_id=learner, card_id=new.card_id, idempotency_key="d2")


@pytest.fixture
def cards_client(settings: Settings, pg_db: Db) -> Callable[[UUID], Awaitable[httpx.AsyncClient]]:
    async def make(learner_id: UUID) -> httpx.AsyncClient:
        app = create_app(settings)
        app.state.resources = build_app_resources(settings, None, None, db=pg_db)
        app.dependency_overrides[enforce_action_rate_limit] = lambda: None
        cookie = session_cookie(await session_token(pg_db, learner_id, settings))
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=cookie
        )

    return make


async def test_cards_routes(
    pg_db: Db, settings: Settings, cards_client: Callable[[UUID], Awaitable[httpx.AsyncClient]]
) -> None:
    """§6.4 «Артефакты»: `GET /v1/topics/{id}/cards`, `PATCH/DELETE /v1/cards/{id}`; another
    learner — 404."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    card = await _card(pg_db, learner, "Root test when?")
    await write_system_cards(
        pg_db, settings, learner_id=learner, topic_id=topic_id, cards=[card], created_by=WRITER
    )
    async with await cards_client(learner) as client:
        listed = (await client.get(f"/v1/topics/{topic_id}/cards")).json()
        assert listed["version"] == 1
        assert listed["cards"] == [
            {
                "card_id": str(card.card_id), "kind": "sign_to_method", "prompt": "Root test when?",
                "owner": "system", "generatedBy": None,
            }
        ]  # fmt: skip
        edit = {"prompt": "When the root test?", "idempotency_key": "p1"}
        patched = await client.patch(f"/v1/cards/{card.card_id}", json=edit)
        assert patched.status_code == 200 and patched.json()["version"] == 2
        assert (await client.patch(f"/v1/cards/{card.card_id}", json=edit)).json() == (
            patched.json()
        )
        new_id = patched.json()["cards"][0]["card_id"]
        drop = {"idempotency_key": "x1"}
        deleted = await client.request("DELETE", f"/v1/cards/{new_id}", json=drop)
        assert (deleted.json()["version"], deleted.json()["cards"]) == (3, [])
    async with await cards_client(other) as client:
        assert (await client.get(f"/v1/topics/{topic_id}/cards")).status_code == 404
        foreign = {"prompt": "x", "idempotency_key": "p1"}
        assert (await client.patch(f"/v1/cards/{card.card_id}", json=foreign)).status_code == 404
