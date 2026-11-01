"""Event catalog of Phase 4 (ТЗ Ф4 §8.3: T-EV1, T-EV2, T-EV3, T-EV5; WP2.1–2.4)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from quack.db import Db
from quack.events.catalog import EVENT_TYPES as PHASE_1_3_TYPES
from quack.events.keys import (
    check_failed_key,
    embedding_key,
    episode_key,
    observation_key,
    today_key,
    verdict_key,
)
from quack.events.log import NewEvent, append_events
from quack.events.registry import (
    REGISTRY,
    SCHEMA_VERSION,
    SPECS,
    TEXT_TYPES,
    payload_model,
    validate_payload,
)
from quack.events.values import CheckFailed, Verdict
from tests.db.fixtures import MIGRATIONS_DIR, create_learner
from tests.events.samples import SAMPLES, sample_payload

# ТЗ Ф4 §6.3: type -> (projector handler, dispatcher rule). «—» is False.
CATALOG: dict[str, tuple[bool, bool]] = {
    "message.sent": (False, True),
    "answer.submitted": (True, True),
    "element.responded": (False, True),
    "canvas.line_committed": (False, True),
    "canvas.line_erased": (False, True),
    "voice.transcribed": (False, True),
    "photo.uploaded": (False, True),
    "confidence.reported": (False, False),
    "hint.requested": (False, True),
    "solution.requested": (False, True),
    "check.skipped": (False, True),
    "item.flagged_unclear": (False, True),
    "prep_mode.enabled": (False, False),
    "prep_mode.disabled": (False, False),
    "grade.reported": (False, False),
    "turn.committed": (True, True),
    "verdict.recorded": (True, True),
    "check.failed": (False, True),
    "observation.extracted": (True, True),
    "observation.diagnosed": (True, True),
    "observation.second_opinion": (True, False),
    "domain.created": (True, False),
    "topic.proposed": (True, True),
    "topic.created": (True, True),
    "topic.merged": (True, False),
    "topic.split": (True, False),
    "topic.renamed": (True, False),
    "topic.hidden": (True, False),
    "skill.created": (True, True),
    "skill.merged": (True, True),
    "skill.split": (True, True),
    "skill.merge_suggested": (False, False),
    "edge.added": (True, False),
    "edge.removed": (True, False),
    "hub.created": (True, False),
    "hub.deadline_set": (True, True),
    "hub.topics_changed": (True, False),
    "task.created": (True, True),
    "task.verified": (True, False),
    "task.key_disputed": (True, False),
    "task.markup_disputed": (True, False),
    "material.uploaded": (True, True),
    "material.parsed": (True, True),
    "chunk.indexed": (True, True),
    "dispute.filed": (True, False),
    "typo.declared": (True, False),
    "known.claimed": (True, True),
    "handwriting.corrected": (False, True),
    "episode.reassigned": (False, True),
    "preference.set": (True, False),
    "consent.given": (False, False),
    "consent.revoked": (False, False),
    "session.started": (False, True),
    "session.ended": (True, True),
    "hint.shown": (False, True),
    "today.shown": (False, True),
    "task.closed": (True, True),
    "episode.closed": (False, True),
    "agent.requested": (False, True),
    "agent.completed": (False, False),
    "digest.written": (True, True),
    "misconception.described": (True, True),
    "misconception.merged": (True, False),
    "artifact.updated": (False, False),
    "embedding.ready": (True, False),
}
NOT_REGISTERED = ("domain.assigned", "hub.suggested", "hub.accepted", "topic.moved")


def _loaded() -> dict[tuple[str, int], Any]:
    payload_model("message.sent")  # import the catalog
    return dict(REGISTRY)


def test_catalog_matches_spec() -> None:
    registry = _loaded()
    assert {t for t, _ in registry} == set(CATALOG)
    assert {v for _, v in registry} == {SCHEMA_VERSION} == {1}
    # No type of С-§5 that §6.3 does not name (names §6.3 reuses are new definitions).
    assert not (set(PHASE_1_3_TYPES) - set(CATALOG)) & {t for t, _ in registry}
    for name in NOT_REGISTERED:
        assert (name, 1) not in registry


def test_samples_cover_catalog() -> None:
    assert set(SAMPLES) == set(CATALOG)


@pytest.mark.parametrize("event_type", sorted(CATALOG))
def test_invalid_payload_rejected(event_type: str) -> None:
    validate_payload(event_type, sample_payload(event_type))
    extra = sample_payload(event_type) | {"unexpected_field": 1}
    with pytest.raises(ValidationError):
        validate_payload(event_type, extra)
    model = payload_model(event_type)
    required = [n for n, f in model.model_fields.items() if f.is_required()]
    for name in required:
        broken = sample_payload(event_type)
        broken.pop(name)
        with pytest.raises(ValidationError):
            validate_payload(event_type, broken)


async def test_invalid_payload_is_not_written(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    bad = NewEvent(type="message.sent", payload={"txt": "hi"}, actor="learner", idempotency_key="k")
    with pytest.raises(ValidationError):
        await append_events(pg_db, learner, [bad])
    assert (
        await pg_db.fetch_all(
            "SELECT seq FROM core.events WHERE learner_id = %(learner_id)s", {"learner_id": learner}
        )
        == []
    )


def test_shared_models_are_the_payloads() -> None:
    """Q-A-1 (a): stream B writes its `Verdict` / `CheckFailed` objects as they are."""
    assert payload_model("verdict.recorded") is Verdict
    assert payload_model("check.failed") is CheckFailed
    verdict = Verdict.model_validate(sample_payload("verdict.recorded"))
    assert validate_payload("verdict.recorded", verdict) is verdict


def test_derived_keys_deterministic() -> None:
    session, node = uuid4(), uuid4()
    pairs = [
        (observation_key(9, "extractor@3", 12), observation_key(9, "extractor@3", 12)),
        (verdict_key(5, "a"), verdict_key(5, "a")),
        (check_failed_key(5, "a"), check_failed_key(5, "a")),
        (episode_key(session, 20), episode_key(session, 20)),
        (embedding_key("skill", node, "m"), embedding_key("skill", node, "m")),
    ]
    for first, second in pairs:
        assert first == second
    assert observation_key(9, "extractor@3", 12) == "obs:9:extractor@3:12"
    assert verdict_key(5, "a") != verdict_key(5, "b") != check_failed_key(5, "b")
    items = [{"skill_id": str(node), "reasons": ["due"]}]
    day = date(2026, 11, 2)
    assert today_key(node, day, items) == today_key(node, day, [dict(items[0])])
    assert today_key(node, day, items) != today_key(node, day, [{"skill_id": "x"}])


async def test_derived_key_retry_returns_same_event(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    event = NewEvent(
        type="verdict.recorded",
        payload=sample_payload("verdict.recorded"),
        actor="code",
        idempotency_key=verdict_key(5, "main"),
    )
    [first] = await append_events(pg_db, learner, [event])
    [again] = await append_events(pg_db, learner, [event])
    assert again == first


def test_registry_completeness() -> None:
    _loaded()
    assert {k[0]: (s.projector, s.dispatcher) for k, s in SPECS.items()} == CATALOG


def test_text_types_have_text_and_match_index() -> None:
    for event_type in TEXT_TYPES:
        assert "text" in payload_model(event_type).model_fields, event_type
    baseline = next(MIGRATIONS_DIR.glob("*_baseline.sql")).read_text(encoding="utf-8")
    index = re.search(r"idx_events_fts_en[\s\S]*?WHERE type IN \(([\s\S]*?)\);", baseline)
    assert index is not None
    assert set(re.findall(r"'([a-z_.]+)'", index.group(1))) == set(TEXT_TYPES)
