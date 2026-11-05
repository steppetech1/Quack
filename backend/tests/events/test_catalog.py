"""Event catalog of Phase 4 (ТЗ Ф4 §8.3: T-EV1, T-EV2, T-EV3, T-EV5; WP2.1–2.4)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from quack.db import Db
from quack.events.actor import is_valid_actor
from quack.events.hashing import payload_hash
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
    UnknownEventTypeError,
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
# The types of Phases 1–3 that §6.3 does not name (the names it reuses are new definitions).
REMOVED_PHASE_1_3_TYPES = frozenset(
    {
        "artifact.cheatsheet_updated", "artifact.note_created", "artifact.note_deleted",
        "artifact.note_updated", "diagnostic.completed", "diagnostic.question_answered",
        "diagnostic.started", "domain.archived", "evidence.superseded",
        "learner.preferences_updated", "learner.registered", "misconception.resolved",
        "session.abandoned", "session.checkpointed", "session.completed",
        "session.digest_created", "skill.promoted", "structure.confirmed", "structure.edited",
        "structure.interview_completed", "structure.proposed", "task.assigned",
        "task.evaluated", "task.generated", "task.hint_given", "task.submitted",
        "teacher.concept_explained", "topic.status_changed", "turn.extracted",
        "turn.student_replied", "turn.teacher_replied",
    }
)  # fmt: skip


def _loaded() -> dict[tuple[str, int], Any]:
    payload_model("message.sent")  # import the catalog
    return dict(REGISTRY)


def test_catalog_matches_spec() -> None:
    registry = _loaded()
    assert {t for t, _ in registry} == set(CATALOG)
    assert {v for _, v in registry} == {SCHEMA_VERSION} == {1}
    # No type of С-§5 that §6.3 does not name (names §6.3 reuses are new definitions).
    assert not REMOVED_PHASE_1_3_TYPES & {t for t, _ in registry}
    for name in NOT_REGISTERED:
        assert (name, 1) not in registry


def test_samples_cover_catalog() -> None:
    assert set(SAMPLES) == set(CATALOG)


def test_type_names_follow_the_naming_rule() -> None:
    """The catalog names are `noun.verb` in snake case (carried over from the Phase 3 catalog
    test, D-A-227)."""
    for name in CATALOG:
        assert re.fullmatch(r"[a-z]+(_[a-z]+)*\.[a-z]+(_[a-z]+)*", name), name


def test_unknown_type_or_version_is_rejected() -> None:
    """Т-§2.4: an unknown type or a schema version the code does not know is an error, never a
    skip (carried over from the Phase 3 catalog test, D-A-227)."""
    with pytest.raises(UnknownEventTypeError):
        validate_payload("nope.done", {})
    with pytest.raises(UnknownEventTypeError):
        validate_payload("domain.created", sample_payload("domain.created"), 2)


def test_bounded_fields_are_checked() -> None:
    """Closed ranges are enforced when the payload is validated (carried over from the Phase 3
    catalog test, D-A-227): a confidence in 0..1, a hint step in 1..3."""
    edge = {**sample_payload("edge.added"), "confidence": 1.5}
    with pytest.raises(ValidationError):
        validate_payload("edge.added", edge)
    hint = {**sample_payload("hint.shown"), "step": 4}
    with pytest.raises(ValidationError):
        validate_payload("hint.shown", hint)


def _noted(actor: str) -> NewEvent:
    return NewEvent(type="message.sent", payload={}, actor=actor, idempotency_key="k")


@pytest.mark.parametrize(
    "actor",
    ["learner", "system", "code", "teacher@1", "extractor@12", "structurer@1", "agent:analyst"],
)
def test_valid_actors(actor: str) -> None:
    assert is_valid_actor(actor)
    assert _noted(actor).actor == actor


@pytest.mark.parametrize(
    "actor",
    [
        "",
        "teacher",
        "teacher@",
        "teacher@0",
        "Teacher@1",
        "admin",
        "extractor@v1",
        "agent:",
        "Agent:x",
    ],
)
def test_invalid_actors(actor: str) -> None:
    assert not is_valid_actor(actor)
    with pytest.raises(ValidationError):
        _noted(actor)


def test_payload_hash_is_canonical_and_order_independent() -> None:
    a = {"b": 1, "a": {"y": [1, 2], "x": "z"}, "c": None}
    b = {"c": None, "a": {"x": "z", "y": [1, 2]}, "b": 1}
    assert payload_hash(a) == payload_hash(b)
    assert re.fullmatch(r"[0-9a-f]{64}", payload_hash(a))
    assert payload_hash(a) != payload_hash({**a, "b": 2})
    assert payload_hash({"a": [1, 2]}) != payload_hash({"a": [2, 1]})


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


def test_check_failed_unsupported_goes_to_the_model() -> None:
    """Т-§6.6, Т-§22.7 (D-B-146): a form without a structural check is `unsupported`."""
    payload = {**sample_payload("check.failed"), "reason": "unsupported"}
    checked = validate_payload("check.failed", payload)
    assert isinstance(checked, CheckFailed) and checked.reason == "unsupported"


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


@pytest.mark.parametrize(
    ("event_type", "changes"),
    [
        ("canvas.line_committed", {"kind": "formula"}),
        ("artifact.updated", {"kind": "topic_notes"}),
        ("artifact.updated", {"kind": "notes", "topic_id": None, "hub_id": str(uuid4())}),
        ("artifact.updated", {"kind": "cards", "hub_id": str(uuid4())}),
        ("artifact.updated", {"kind": "cheatsheet", "topic_id": None}),
    ],
)
def test_frozen_kinds_reject_other_values(event_type: str, changes: dict[str, Any]) -> None:
    """Q-A-3 (C, G1): `canvas.line_committed.kind` — LaTeX or text (Т-§15.3);
    `artifact.updated.kind` — topic notes and cards with `topic_id`, a hub cheat sheet with
    `hub_id` (WP23.1)."""
    with pytest.raises(ValidationError):
        validate_payload(event_type, {**sample_payload(event_type), **changes})


def test_frozen_kinds_accept_their_values() -> None:
    hub = str(uuid4())
    sheet = {**sample_payload("artifact.updated"), "kind": "cheatsheet", "topic_id": None}
    assert validate_payload("artifact.updated", {**sheet, "hub_id": hub})
    typed = {**sample_payload("canvas.line_committed"), "kind": "text"}
    assert validate_payload("canvas.line_committed", typed)


def test_session_started_marks_a_resumed_session() -> None:
    """ТЗ Ф4 WP13.10 (D-A-121): an action in a session closed by the inactivity sweeper writes
    `session.started {session_id, resumed: true}`; a new session is not resumed."""
    from quack.events.event_types import SessionStarted

    fresh = validate_payload("session.started", sample_payload("session.started"))
    assert isinstance(fresh, SessionStarted) and fresh.resumed is False
    resumed = {**sample_payload("session.started"), "resumed": True}
    back = validate_payload("session.started", resumed)
    assert isinstance(back, SessionStarted) and back.resumed is True


def test_agent_artifact_request_names_the_artifact_and_its_hub() -> None:
    """D-A-196 (#191): `agent.requested {kind: artifact}` carries the artifact and the hub of a
    cheat sheet as data; an unknown artifact is rejected."""
    from uuid import uuid4

    payload = sample_payload("agent.requested")
    payload["kind"] = "artifact"
    payload["params"] = {"artifact": "cheatsheet", "hub_id": str(uuid4())}
    validate_payload("agent.requested", payload, 1)
    payload["params"] = {"artifact": "poster"}
    with pytest.raises(ValidationError):
        validate_payload("agent.requested", payload, 1)


def test_image_registry_has_no_gaps_and_tracks_rollback() -> None:
    """WP2.4 (Д-§5.2 п.6, Д-§6.3, T-EV5, D-A-218): the registry of the image is the Phase 4
    catalog with `rules@3`; every type marked for the projector or the dispatcher has its handler
    or rule; a new pair already written, or other rules, forbid a rollback."""
    from quack.runtime.event_registry import can_rollback, event_registry, registry_gaps

    current = event_registry()
    assert current["rules_version"] == "rules@3"
    assert len(current["events"]) == len(SPECS)
    assert registry_gaps() == []
    assert can_rollback(current, set())["can_rollback"]
    first = current["events"][0]
    previous = {**current, "events": current["events"][1:]}
    seen = {(first["type"], first["schema_version"])}
    verdict = can_rollback(previous, seen)
    assert not verdict["can_rollback"] and verdict["new_pairs_seen"] == [first]
    assert not can_rollback({**current, "rules_version": "rules@2"}, set())["can_rollback"]
