"""Event catalog (spec §5), payload hash (spec §2.2), actor (spec §2.4) and append_events
(spec §8.1, Т-§2.1, Т-§2.3)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.events import IdempotencyConflictError, append_events
from quack.events.actor import is_valid_actor
from quack.events.catalog import EVENT_TYPES, TEXT_EVENT_TYPES, validate_payload
from quack.events.hashing import payload_hash
from quack.events.models import NewEvent
from quack.events.payloads import SkillRef
from tests.factories import new_event, sample_payloads

# --- Catalog ---------------------------------------------------------------------------


def test_catalog_has_exactly_36_event_types() -> None:
    assert len(EVENT_TYPES) == 36
    for name in EVENT_TYPES:
        assert re.fullmatch(r"[a-z]+(_[a-z]+)*\.[a-z]+(_[a-z]+)*", name), name


def test_sample_payloads_cover_whole_catalog() -> None:
    assert set(sample_payloads()) == set(EVENT_TYPES)


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_every_event_type_validates_its_payload(event_type: str) -> None:
    payload = sample_payloads()[event_type]
    model = validate_payload(event_type, payload)
    assert isinstance(model, EVENT_TYPES[event_type])


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_every_event_type_rejects_unknown_fields(event_type: str) -> None:
    payload = dict(sample_payloads()[event_type])
    payload["unexpected_field"] = 1
    with pytest.raises(ValidationError):
        validate_payload(event_type, payload)


@pytest.mark.parametrize("event_type", sorted(EVENT_TYPES))
def test_every_event_type_rejects_missing_required_field(event_type: str) -> None:
    model = EVENT_TYPES[event_type]
    required = [n for n, f in model.model_fields.items() if f.is_required()]
    assert required, f"{event_type} must have required fields"
    payload = dict(sample_payloads()[event_type])
    del payload[required[0]]
    with pytest.raises(ValidationError):
        validate_payload(event_type, payload)


def test_text_events_use_text_field() -> None:
    for event_type in TEXT_EVENT_TYPES:
        assert "text" in EVENT_TYPES[event_type].model_fields, event_type


def test_unknown_event_type_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_payload("material.uploaded", {})
    with pytest.raises(ValueError):
        validate_payload("domain.created", sample_payloads()["domain.created"], 2)


def test_skill_ref_requires_exactly_one_form() -> None:
    SkillRef.model_validate({"skill_id": str(uuid4())})
    SkillRef.model_validate({"new_skill": {"name": "Chain rule", "topic_id": str(uuid4())}})
    with pytest.raises(ValidationError):
        SkillRef.model_validate({})
    with pytest.raises(ValidationError):
        SkillRef.model_validate(
            {"skill_id": str(uuid4()), "new_skill": {"name": "x", "topic_id": str(uuid4())}}
        )
    with pytest.raises(ValidationError):
        SkillRef.model_validate({"new_skill": {"name": "", "topic_id": str(uuid4())}})


def test_unit_interval_fields_are_bounded() -> None:
    payload = dict(sample_payloads()["task.evaluated"])
    payload["score"] = 1.5
    with pytest.raises(ValidationError):
        validate_payload("task.evaluated", payload)
    hint = dict(sample_payloads()["task.hint_given"])
    hint["hint_level"] = 4
    with pytest.raises(ValidationError):
        validate_payload("task.hint_given", hint)


# --- Actor -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "actor", ["learner", "system", "code", "teacher@1", "extractor@12", "structurer@1"]
)
def test_valid_actors(actor: str) -> None:
    assert is_valid_actor(actor)
    new_event("domain.created", sample_payloads()["domain.created"], actor=actor)


@pytest.mark.parametrize(
    "actor", ["", "teacher", "teacher@", "teacher@0", "Teacher@1", "admin", "extractor@v1"]
)
def test_invalid_actors(actor: str) -> None:
    assert not is_valid_actor(actor)
    with pytest.raises(ValidationError):
        new_event("domain.created", sample_payloads()["domain.created"], actor=actor)


# --- Payload hash ----------------------------------------------------------------------


def test_payload_hash_is_canonical_and_order_independent() -> None:
    a = {"b": 1, "a": {"y": [1, 2], "x": "z"}, "c": None}
    b = {"c": None, "a": {"x": "z", "y": [1, 2]}, "b": 1}
    assert payload_hash(a) == payload_hash(b)
    assert re.fullmatch(r"[0-9a-f]{64}", payload_hash(a))
    assert payload_hash(a) != payload_hash({**a, "b": 2})
    assert payload_hash({"a": [1, 2]}) != payload_hash({"a": [2, 1]})


# --- append_events ---------------------------------------------------------------------


async def _append(db: SessionFactory, learner: UUID, events: list[NewEvent], **kw: Any):
    async with db() as session, session.begin():
        return await append_events(session, learner, events, **kw)


async def _count(db: SessionFactory, sql: str, **params: Any) -> int:
    async with db() as session:
        return int((await session.execute(text(sql), params)).scalar_one())


def _domain_created(domain_id: UUID, slug: str = "calculus", **kw: Any) -> NewEvent:
    return new_event(
        "domain.created",
        {"domain_id": str(domain_id), "name": "Calculus", "slug": slug},
        domain_id=domain_id,
        **kw,
    )


async def test_append_assigns_monotonic_gapless_seq(db: SessionFactory) -> None:
    learner = uuid4()
    first = await _append(db, learner, [_domain_created(uuid4(), f"s{i}") for i in range(3)])
    second = await _append(db, learner, [_domain_created(uuid4(), f"t{i}") for i in range(2)])
    assert [e.seq for e in first + second] == [1, 2, 3, 4, 5]
    assert (
        await _count(db, "SELECT last_seq FROM core.learner_seq WHERE learner_id = :l", l=learner)
        == 5
    )
    other = await _append(db, uuid4(), [_domain_created(uuid4())])
    assert other[0].seq == 1


async def test_append_stores_envelope_and_hash(db: SessionFactory) -> None:
    learner, domain = uuid4(), uuid4()
    [event] = await _append(db, learner, [_domain_created(domain, actor="structurer@1")])
    assert event.actor == "structurer@1"
    assert event.domain_id == domain
    assert event.payload["has_materials"] is False
    assert event.payload_hash == payload_hash(event.payload)
    assert event.schema_version == 1


async def test_idempotent_retry_returns_existing_event(db: SessionFactory) -> None:
    learner, domain = uuid4(), uuid4()
    event = _domain_created(domain, key="client-key-1")
    [first] = await _append(db, learner, [event])
    reordered = new_event(
        "domain.created",
        {"slug": "calculus", "name": "Calculus", "domain_id": str(domain)},
        key="client-key-1",
        domain_id=domain,
    )
    [again] = await _append(db, learner, [reordered])
    assert again.seq == first.seq
    assert again.event_id == first.event_id
    assert (
        await _count(db, "SELECT count(*) FROM core.events WHERE learner_id = :l", l=learner) == 1
    )
    assert (
        await _count(db, "SELECT count(*) FROM core.domains WHERE learner_id = :l", l=learner) == 1
    )


async def test_idempotency_conflict_on_changed_payload(db: SessionFactory) -> None:
    learner, domain = uuid4(), uuid4()
    await _append(db, learner, [_domain_created(domain, key="k1")])
    changed = new_event(
        "domain.created",
        {"domain_id": str(domain), "name": "Algebra", "slug": "calculus"},
        key="k1",
    )
    with pytest.raises(IdempotencyConflictError):
        await _append(db, learner, [_domain_created(uuid4(), "x", key="k2"), changed])
    # Nothing from the failed transaction is committed.
    assert (
        await _count(db, "SELECT count(*) FROM core.events WHERE learner_id = :l", l=learner) == 1
    )
    assert (
        await _count(db, "SELECT last_seq FROM core.learner_seq WHERE learner_id = :l", l=learner)
        == 1
    )


async def test_mixed_batch_returns_existing_and_appends_new(db: SessionFactory) -> None:
    learner = uuid4()
    [old] = await _append(db, learner, [_domain_created(uuid4(), "a", key="k1")])
    replay = new_event("domain.created", old.payload, key="k1")
    result = await _append(db, learner, [replay, _domain_created(uuid4(), "b", key="k2")])
    assert [e.seq for e in result] == [1, 2]
    assert result[0].event_id == old.event_id


async def test_invalid_payload_writes_nothing(db: SessionFactory) -> None:
    learner = uuid4()
    bad = new_event("domain.created", {"domain_id": str(uuid4()), "name": "x"})
    with pytest.raises(ValidationError):
        await _append(db, learner, [bad])
    assert (
        await _count(db, "SELECT count(*) FROM core.events WHERE learner_id = :l", l=learner) == 0
    )


async def test_event_types_seen_recorded(db: SessionFactory) -> None:
    await _append(db, uuid4(), [_domain_created(uuid4())])
    assert (
        await _count(db, "SELECT count(*) FROM ops.event_types_seen WHERE type = 'domain.created'")
        == 1
    )


async def test_concurrent_appends_for_one_learner_are_serialized(db: SessionFactory) -> None:
    learner = uuid4()
    batches = [[_domain_created(uuid4(), f"c{i}-{j}") for j in range(3)] for i in range(5)]
    results = await asyncio.gather(*(_append(db, learner, b) for b in batches))
    seqs = sorted(e.seq for batch in results for e in batch)
    assert seqs == list(range(1, 16))
    for batch in results:
        assert [e.seq for e in batch] == list(range(batch[0].seq, batch[0].seq + 3))


# --- Relational projections ------------------------------------------------------------


async def _row(db: SessionFactory, sql: str, **params: Any) -> dict[str, Any]:
    async with db() as session:
        row = (await session.execute(text(sql), params)).mappings().one()
        return dict(row)


async def test_domain_projection_follows_events(db: SessionFactory) -> None:
    learner, domain = uuid4(), uuid4()
    d = str(domain)
    await _append(db, learner, [_domain_created(domain)])
    row = await _row(db, "SELECT * FROM core.domains WHERE id = :id", id=domain)
    assert row["status"] == "onboarding" and row["created_seq"] == 1 and row["slug"] == "calculus"
    await _append(
        db,
        learner,
        [
            new_event(
                "structure.interview_completed",
                {"domain_id": d, "goals": "g", "background": "b", "focus_areas": []},
            )
        ],
    )
    assert (await _row(db, "SELECT status FROM core.domains WHERE id = :id", id=domain))[
        "status"
    ] == "structuring"
    await _append(
        db, learner, [new_event("structure.confirmed", {"domain_id": d, "structure_version": 3})]
    )
    row = await _row(db, "SELECT * FROM core.domains WHERE id = :id", id=domain)
    assert row["status"] == "active"
    assert row["active_structure_version"] == 3
    assert row["updated_seq"] == 3
    await _append(
        db, learner, [new_event("structure.edited", {"domain_id": d, "structure_version": 4})]
    )
    row = await _row(db, "SELECT * FROM core.domains WHERE id = :id", id=domain)
    assert row["active_structure_version"] == 4
    await _append(db, learner, [new_event("domain.archived", {"domain_id": d})])
    row = await _row(db, "SELECT * FROM core.domains WHERE id = :id", id=domain)
    assert row["status"] == "archived" and row["updated_seq"] == 5


def _session_started(session_id: UUID, domain: UUID) -> NewEvent:
    return new_event(
        "session.started",
        {
            "session_id": str(session_id),
            "domain_id": str(domain),
            "topic_id": None,
            "mode": "practice",
            "goal": "drill",
            "plan": [{"step": 1}],
        },
    )


async def test_session_projection_follows_events(db: SessionFactory) -> None:
    learner, domain, sid = uuid4(), uuid4(), uuid4()
    s = str(sid)
    await _append(db, learner, [_domain_created(domain), _session_started(sid, domain)])
    row = await _row(db, "SELECT * FROM core.sessions WHERE id = :id", id=sid)
    assert row["status"] == "active" and row["start_seq"] == 2 and row["mode"] == "practice"
    assert row["plan"] == [{"step": 1}] and row["last_turn_index"] == -1
    turn = str(uuid4())
    await _append(
        db,
        learner,
        [
            new_event(
                "turn.student_replied",
                {"session_id": s, "turn_id": turn, "turn_index": 0, "text": "hi"},
            ),
            new_event(
                "turn.teacher_replied",
                {
                    "session_id": s,
                    "turn_id": turn,
                    "turn_index": 1,
                    "text": "hello",
                    "pedagogical_move": "greet",
                },
            ),
            new_event(
                "session.checkpointed",
                {
                    "session_id": s,
                    "up_to_turn_index": 1,
                    "rolling_summary": "x",
                    "open_threads": [],
                    "key_observations": [],
                },
            ),
        ],
    )
    row = await _row(db, "SELECT * FROM core.sessions WHERE id = :id", id=sid)
    assert row["last_turn_index"] == 1 and row["last_checkpoint_turn"] == 1
    await _append(
        db,
        learner,
        [
            new_event(
                "session.completed",
                {"session_id": s, "domain_id": str(domain), "summary_stats": {}},
            )
        ],
    )
    row = await _row(db, "SELECT * FROM core.sessions WHERE id = :id", id=sid)
    assert row["status"] == "completed" and row["end_seq"] == 6 and row["ended_at"] is not None


async def test_only_one_active_session_per_learner(db: SessionFactory) -> None:
    learner, domain = uuid4(), uuid4()
    first, second = uuid4(), uuid4()
    await _append(db, learner, [_session_started(first, domain)])
    with pytest.raises(Exception):  # noqa: B017 - unique violation from the partial index
        await _append(db, learner, [_session_started(second, domain)])
    abandon = new_event(
        "session.abandoned",
        {
            "session_id": str(first),
            "domain_id": str(domain),
            "reason": "superseded_by_new_session",
        },
    )
    await _append(db, learner, [abandon, _session_started(second, domain)])
    rows = {
        r["id"]: r["status"]
        for r in (
            await _rows(db, "SELECT id, status FROM core.sessions WHERE learner_id = :l", l=learner)
        )
    }
    assert rows == {first: "abandoned", second: "active"}


async def _rows(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def test_task_bank_projection(db: SessionFactory) -> None:
    learner = uuid4()
    payload = sample_payloads()["task.generated"]
    [event] = await _append(db, learner, [new_event("task.generated", payload)])
    row = await _row(db, "SELECT * FROM core.task_bank WHERE id = :id", id=UUID(payload["task_id"]))
    assert row["family_id"] == "limits-basic"
    assert row["kind"] == "math"
    assert row["created_seq"] == event.seq
    assert row["reference_solution"] == {"sympy": "0"}
    assert row["target_skills"] == json.loads(json.dumps(event.payload["target_skills"]))
    assert row["target_misconceptions"] == []


async def test_artifact_projections(db: SessionFactory) -> None:
    learner = uuid4()
    samples = sample_payloads()
    note = samples["artifact.note_created"]
    art = UUID(note["artifact_id"])
    await _append(db, learner, [new_event("artifact.note_created", note)])
    row = await _row(db, "SELECT * FROM core.artifacts WHERE id = :id", id=art)
    assert row["kind"] == "note" and row["content_md"] == "my note" and row["version"] == 1
    await _append(
        db, learner, [new_event("artifact.note_updated", samples["artifact.note_updated"])]
    )
    row = await _row(db, "SELECT * FROM core.artifacts WHERE id = :id", id=art)
    assert row["content_md"] == "edited" and row["version"] == 2 and row["source_seq"] == 2
    await _append(db, learner, [new_event("artifact.note_deleted", {"artifact_id": str(art)})])
    row = await _row(db, "SELECT * FROM core.artifacts WHERE id = :id", id=art)
    assert row["is_deleted"] is True

    sheet = dict(samples["artifact.cheatsheet_updated"])
    sheet["artifact_id"] = str(uuid4())
    await _append(db, learner, [new_event("artifact.cheatsheet_updated", sheet)])
    await _append(
        db,
        learner,
        [new_event("artifact.cheatsheet_updated", {**sheet, "version": 2, "text": "v2"})],
    )
    row = await _row(
        db, "SELECT * FROM core.artifacts WHERE id = :id", id=UUID(sheet["artifact_id"])
    )
    assert row["kind"] == "cheatsheet" and row["version"] == 2 and row["content_md"] == "v2"

    digest = dict(samples["session.digest_created"])
    digest["artifact_id"] = str(uuid4())
    await _append(db, learner, [new_event("session.digest_created", digest)])
    row = await _row(
        db, "SELECT * FROM core.artifacts WHERE id = :id", id=UUID(digest["artifact_id"])
    )
    assert row["kind"] == "digest" and row["session_id"] == UUID(digest["session_id"])


async def test_wakeup_is_published_after_commit(db: SessionFactory, redis: Redis) -> None:
    learner = uuid4()
    pubsub = redis.pubsub()
    await pubsub.subscribe("projector:wakeup")
    await pubsub.get_message(timeout=1.0)  # subscribe confirmation
    async with db() as session, session.begin():
        events = await append_events(
            session, learner, [_domain_created(uuid4()), _domain_created(uuid4(), "b")], redis=redis
        )
        assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.3) is None
    message = None
    for _ in range(20):
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
        if message:
            break
    assert message is not None
    assert json.loads(message["data"]) == {"learner_id": str(learner), "seq": events[-1].seq}
    await pubsub.aclose()


async def test_wakeup_not_published_on_rollback(db: SessionFactory, redis: Redis) -> None:
    pubsub = redis.pubsub()
    await pubsub.subscribe("projector:wakeup")
    await pubsub.get_message(timeout=1.0)
    async with db() as session:
        await session.begin()
        await append_events(session, uuid4(), [_domain_created(uuid4())], redis=redis)
        await session.rollback()
    assert await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5) is None
    await pubsub.aclose()
