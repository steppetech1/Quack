"""Learner notes (ТЗ §2.3, §3): `artifact.note_created` / `_updated` / `_deleted` and the
listing of active artifacts from core.artifacts."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID, uuid5

from sqlalchemy import text

from quack.api.app import AppResources
from quack.api.errors import ApiError
from quack.artifacts.schemas import (
    ArtifactListResponse,
    ArtifactView,
    NoteCreateRequest,
    NoteCreateResponse,
    NoteDeleteRequest,
    NoteDeleteResponse,
    NoteUpdateRequest,
    NoteUpdateResponse,
)
from quack.artifacts.store import ARTIFACT_VIEW_COLUMNS, event_key, fetch_rows, stored_event
from quack.events import NewEvent, StoredEvent, append_events
from quack.graph.ids import QUACK_UUID_NS

# A9: notes are written by the learner.
NOTE_ACTOR = "learner"


async def create_note(
    learner_id: UUID, body: NoteCreateRequest, resources: AppResources
) -> NoteCreateResponse:
    """`artifact.note_created` with the deterministic note id (A11)."""
    key = event_key(body.idempotency_key, "artifact.note_created")
    replay = await stored_event(resources.session_factory, learner_id, key)
    if replay is None:
        artifact_id = uuid5(QUACK_UUID_NS, f"note:{learner_id}:{body.idempotency_key}")
        event = NewEvent(
            type="artifact.note_created",
            payload={
                "artifact_id": str(artifact_id),
                "domain_id": str(body.domain_id),
                "topic_id": None if body.topic_id is None else str(body.topic_id),
                "session_id": None if body.session_id is None else str(body.session_id),
                "title": body.title,
                "text": body.text,
            },
            actor=NOTE_ACTOR,
            idempotency_key=key,
        )
        async with resources.session_factory() as session, session.begin():
            [replay] = await append_events(session, learner_id, [event], redis=resources.redis)
    return NoteCreateResponse(artifact_id=replay.payload["artifact_id"], version=1, seq=replay.seq)


async def _append_to_note(
    resources: AppResources,
    learner_id: UUID,
    artifact_id: UUID,
    build: Callable[[int], NewEvent],
) -> StoredEvent:
    """Lock the learner's active note (404 otherwise) and append `build(current_version)`."""
    async with resources.session_factory() as session, session.begin():
        version = (
            await session.execute(
                text(
                    "SELECT version FROM core.artifacts WHERE id = :id AND learner_id = :l "
                    "AND kind = 'note' AND NOT is_deleted FOR UPDATE"
                ),
                {"id": artifact_id, "l": learner_id},
            )
        ).scalar_one_or_none()
        if version is None:
            raise ApiError("NOT_FOUND", "note not found", status_code=404)
        [stored] = await append_events(
            session, learner_id, [build(int(version))], redis=resources.redis
        )
    return stored


async def update_note(
    learner_id: UUID, artifact_id: UUID, body: NoteUpdateRequest, resources: AppResources
) -> NoteUpdateResponse:
    """`artifact.note_updated` with `version = current + 1`; 404 for a missing or foreign note."""
    key = event_key(body.idempotency_key, "artifact.note_updated")
    stored = await stored_event(resources.session_factory, learner_id, key)
    if stored is None:
        stored = await _append_to_note(
            resources,
            learner_id,
            artifact_id,
            lambda version: NewEvent(
                type="artifact.note_updated",
                payload={
                    "artifact_id": str(artifact_id),
                    "version": version + 1,
                    "title": body.title,
                    "text": body.text,
                },
                actor=NOTE_ACTOR,
                idempotency_key=key,
            ),
        )
    p = stored.payload
    return NoteUpdateResponse(artifact_id=p["artifact_id"], version=p["version"], seq=stored.seq)


async def delete_note(
    learner_id: UUID, artifact_id: UUID, body: NoteDeleteRequest, resources: AppResources
) -> NoteDeleteResponse:
    """`artifact.note_deleted`; 404 for a missing or foreign note."""
    key = event_key(body.idempotency_key, "artifact.note_deleted")
    stored = await stored_event(resources.session_factory, learner_id, key)
    if stored is None:
        stored = await _append_to_note(
            resources,
            learner_id,
            artifact_id,
            lambda _: NewEvent(
                type="artifact.note_deleted",
                payload={"artifact_id": str(artifact_id)},
                actor=NOTE_ACTOR,
                idempotency_key=key,
            ),
        )
    return NoteDeleteResponse(
        artifact_id=stored.payload["artifact_id"], is_deleted=True, seq=stored.seq
    )


async def list_artifacts(
    learner_id: UUID,
    domain_id: UUID | None,
    topic_id: UUID | None,
    kind: str | None,
    resources: AppResources,
) -> ArtifactListResponse:
    """Active (`NOT is_deleted`) artifacts of the learner, filtered (A14)."""
    filters = {"domain_id": domain_id, "topic_id": topic_id, "kind": kind}
    params = {name: value for name, value in filters.items() if value is not None}
    conditions = "".join(f" AND {name} = :{name}" for name in params)
    rows = await fetch_rows(
        resources.session_factory,
        f"SELECT {ARTIFACT_VIEW_COLUMNS} FROM core.artifacts "
        f"WHERE learner_id = :l AND NOT is_deleted{conditions} ORDER BY updated_at DESC",
        l=learner_id,
        **params,
    )
    return ArtifactListResponse(artifacts=[ArtifactView.model_validate(r) for r in rows])
