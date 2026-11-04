"""WP17.4, `T-RV4` (slot 6): the preparation mode is on only by the explicit event
`prep_mode.enabled` of the session's hub; then slot 6 carries what `prep_context` found.
D-B-216."""

from __future__ import annotations

from uuid import UUID

from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.graph.read import MaterialRef, PrepContext, ReviewCandidate
from quack.llm.labels import LabelMap
from quack.session.prep_mode import prep_for_turn, render_prep
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings()


async def _set(pg_db: Db, learner: UUID, kind: str, key: str) -> None:
    await append_events(
        pg_db,
        learner,
        [
            NewEvent(
                type=kind,
                payload={"hub_id": str(fx.HUB)},
                actor="learner",
                idempotency_key=key,
            )
        ],
    )


async def test_prep_slot_only_by_the_explicit_event(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    reader = fx.FixedGraphReader()

    async def prep() -> PrepContext | None:
        return await prep_for_turn(reader, pg_db, learner_id=learner, hub_id=fx.HUB, now_ms=1)

    assert await prep() is None  # a plain visit to the hub
    assert await prep_for_turn(reader, pg_db, learner_id=learner, hub_id=None, now_ms=1) is None
    await _set(pg_db, learner, "prep_mode.enabled", "prep:on")
    found = await prep()
    assert found is not None and found.hub_id == fx.HUB
    await _set(pg_db, learner, "prep_mode.disabled", "prep:off")
    assert await prep() is None

    context = PrepContext(
        hub_id=fx.HUB,
        days_to_deadline=5,
        ready_count=1,
        total_count=3,
        coverage_topic_ids=[fx.TOPIC_OPEN],
        control_materials=[
            MaterialRef(chunk_id=UUID(int=1), material_id=UUID(int=2), kind="past_variant", page=3)
        ],
        candidates=[ReviewCandidate(skill_id=fx.SKILL_FAILED, priority=1.0, reasons=["retention"])],
    )
    labels = LabelMap.build(
        SETTINGS, skills=[fx.SKILL_FAILED], topics=[fx.TOPIC_OPEN], hubs=[fx.HUB]
    )
    text = render_prep(context, labels, {fx.HUB: "Exam"}, SETTINGS)
    assert "H1 «Exam»; 5 days to the deadline" in text
    assert "Ready topics: 1 of 3" in text
    assert "Coverage: T1" in text
    assert "a past exam variant, page 3" in text
    assert "Skills to repeat first: S1" in text
    assert str(fx.HUB) not in text
