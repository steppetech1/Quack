"""WP23.5: the cheat sheet offer is a state read from the journal, not a job
(ТЗ Ф4 WP23.5; Т-§17.7; D-C-144)."""

from __future__ import annotations

from uuid import UUID, uuid4

from quack.artifacts.documents import SystemBlock, write_system_blocks
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.graph.read.cheatsheet_offer import cheatsheet_offer
from tests.artifacts.hubs import DAY_MS, create_hub, set_deadline
from tests.db.fixtures import create_learner

NOW_MS = 1_800_000_000_000


async def _offer(db: Db, learner: UUID, hub: UUID, settings: Settings) -> bool:
    return await cheatsheet_offer(db, learner, hub, settings, now_ms=NOW_MS)


async def test_offer_when_the_deadline_is_within_offer_days(pg_db: Db, settings: Settings) -> None:
    """The deadline is not farther than `cheatsheet_offer_days` and nothing was agreed yet."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    assert not await _offer(pg_db, learner, hub, settings)  # no deadline
    await set_deadline(pg_db, learner, hub, NOW_MS + (settings.cheatsheet_offer_days - 1) * DAY_MS)
    assert await _offer(pg_db, learner, hub, settings)


async def test_no_offer_far_from_or_after_the_deadline(pg_db: Db, settings: Settings) -> None:
    learner = await create_learner(pg_db)
    far, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, far, NOW_MS + (settings.cheatsheet_offer_days + 1) * DAY_MS)
    assert not await _offer(pg_db, learner, far, settings)
    past, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, past, NOW_MS - DAY_MS)
    assert not await _offer(pg_db, learner, past, settings)


async def test_the_latest_deadline_decides(pg_db: Db, settings: Settings) -> None:
    """A moved deadline moves the offer; a removed one (`deadline_at_ms = None`) ends it."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, hub, NOW_MS + DAY_MS)
    await set_deadline(pg_db, learner, hub, NOW_MS + 30 * DAY_MS)
    assert not await _offer(pg_db, learner, hub, settings)
    await set_deadline(pg_db, learner, hub, NOW_MS + 2 * DAY_MS)
    assert await _offer(pg_db, learner, hub, settings)
    await set_deadline(pg_db, learner, hub, None)
    assert not await _offer(pg_db, learner, hub, settings)


async def test_offer_follows_the_setting(pg_db: Db, settings: Settings) -> None:
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, hub, NOW_MS + 10 * DAY_MS)
    assert not await _offer(
        pg_db, learner, hub, settings.model_copy(update={"cheatsheet_offer_days": 7})
    )
    assert await _offer(
        pg_db, learner, hub, settings.model_copy(update={"cheatsheet_offer_days": 14})
    )


async def test_no_offer_once_a_cheat_sheet_exists(pg_db: Db, settings: Settings) -> None:
    """`artifact.updated` of kind `cheatsheet` on the hub ends the offer (D-C-144)."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, hub, NOW_MS + DAY_MS)
    assert await _offer(pg_db, learner, hub, settings)
    block = SystemBlock(kind="summary", content="Formulas")
    await write_system_blocks(
        pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub, blocks=[block],
        created_by="artifact_writer@1",
    )  # fmt: skip
    assert not await _offer(pg_db, learner, hub, settings)


async def test_no_offer_once_the_learner_agreed(pg_db: Db, settings: Settings) -> None:
    """The consent is `agent.requested {kind: artifact, params: {artifact: cheatsheet, hub_id}}`
    (D-C-143, D-C-144); the request of another hub does not end this hub's offer."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    other, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, hub, NOW_MS + DAY_MS)

    async def agree(hub_id: object) -> None:
        payload = {
            "request_id": uuid4(),
            "kind": "artifact",
            "params": {"artifact": "cheatsheet", "hub_id": hub_id},
            "needed_in_session": False,
        }
        event = NewEvent(
            type="agent.requested", payload=payload, actor="learner", idempotency_key=f"a:{uuid4()}"
        )
        await append_events(pg_db, learner, [event])

    await agree(other)
    assert await _offer(pg_db, learner, hub, settings)
    await agree(hub)
    assert not await _offer(pg_db, learner, hub, settings)


async def test_the_offer_never_crosses_learners(pg_db: Db, settings: Settings) -> None:
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    await set_deadline(pg_db, learner, hub, NOW_MS + DAY_MS)
    assert await _offer(pg_db, learner, hub, settings)
    assert not await _offer(pg_db, stranger, hub, settings)
