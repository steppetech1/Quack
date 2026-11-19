"""Routes of export (ТЗ Ф4 WP23.7, §6.4 «Артефакты»; Т-§19.6, Т-§19.7; D-C-240).

`GET /v1/topics/{id}/export/obsidian` — the notes of the topic as a zip vault;
`GET /v1/topics/{id}/export/anki` — its fixed cards as an Anki deck (`.apkg`);
`GET /v1/learner/exports/{id}` — the state of the archive of the learner's data and, when it is
ready, a signed link of the storage that lives until the archive expires (`export_link_ttl_hours`).
All three only read. `POST /v1/learner/export` is added with the kind `data_export` of
`agent.requested` (stream A, D-C-231).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import AppResources, get_resources
from quack.artifacts.cards import topic_cards
from quack.artifacts.documents import ArtifactNotFound, get_artifact
from quack.artifacts.topic_export import (
    anki_cards,
    anki_deck,
    load_topic_info,
    note_name,
    obsidian_vault,
)
from quack.graph.read import GraphReader, create_graph_reader

# Router-level auth: 401 before body validation (ТЗ Фазы 3 WP1.2, T-A2).
router = APIRouter(
    prefix="/v1",
    tags=["artifacts"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]

_EXPORT = (
    "SELECT status, object_key, expires_at FROM content.exports "
    "WHERE export_id = %(export_id)s AND learner_id = %(learner_id)s"
)
_REQUESTED = (
    "SELECT 1 AS found FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'agent.requested' AND payload ->> 'request_id' = %(export_id)s "
    "AND payload ->> 'kind' = 'data_export' LIMIT 1"
)

_BINARY = {"schema": {"type": "string", "format": "binary"}}
_ZIP = {"description": "The vault", "content": {"application/zip": _BINARY}}
_APKG = {"description": "The deck", "content": {"application/octet-stream": _BINARY}}


def get_export_reader(resources: Resources, learner_id: CurrentLearnerId) -> GraphReader:
    """The graph reader of the learner; a dependency, so tests put a fixed one."""
    return create_graph_reader(
        resources.neo4j, resources.settings, learner_id=learner_id, db=resources.db
    )


type ExportReader = Annotated[GraphReader, Depends(get_export_reader)]


class ExportStatus(BaseModel):
    """`pending` — requested, the archive is not ready; `ready` — `download_url` lives
    `expires_in_s`; `expired` — the archive is deleted or past its term (build a new one)."""

    export_id: UUID
    status: Literal["pending", "ready", "expired"]
    download_url: str | None
    expires_in_s: int | None
    expires_at: datetime | None


def _not_found(exc: ArtifactNotFound) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


def _attachment(
    name: str, extension: str, topic_id: UUID, media_type: str, data: bytes
) -> Response:
    ascii_name = f"topic-{topic_id}.{extension}"
    utf8_name = quote(f"{note_name(name, str(topic_id))}.{extension}")
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{utf8_name}"
    return Response(data, media_type=media_type, headers={"Content-Disposition": disposition})


@router.get("/topics/{topic_id}/export/obsidian", response_class=Response, responses={200: _ZIP})
async def export_obsidian(
    topic_id: UUID, learner_id: CurrentLearnerId, resources: Resources, reader: ExportReader
) -> Response:
    try:
        notes = await get_artifact(
            resources.db, learner_id=learner_id, kind="notes", owner_id=topic_id
        )
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    info = await load_topic_info(
        resources.db, reader, learner_id=learner_id, topic_id=topic_id, domain_id=notes.domain_id
    )
    vault = obsidian_vault(notes, info, exported_on=datetime.now(UTC).date())
    return _attachment(info.name, "zip", topic_id, "application/zip", vault)


@router.get("/topics/{topic_id}/export/anki", response_class=Response, responses={200: _APKG})
async def export_anki(
    topic_id: UUID, learner_id: CurrentLearnerId, resources: Resources, reader: ExportReader
) -> Response:
    try:
        cards = await topic_cards(resources.db, learner_id=learner_id, topic_id=topic_id)
    except ArtifactNotFound as exc:
        raise _not_found(exc) from exc
    info = await load_topic_info(
        resources.db, reader, learner_id=learner_id, topic_id=topic_id, domain_id=cards.domain_id
    )
    deck = anki_deck(info, await anki_cards(resources.db, learner_id=learner_id, cards=cards))
    return _attachment(info.name, "apkg", topic_id, "application/octet-stream", deck)


@router.get("/learner/exports/{export_id}", response_model=ExportStatus)
async def get_export(
    export_id: UUID, learner_id: CurrentLearnerId, resources: Resources
) -> ExportStatus:
    row = await resources.db.fetch_one(_EXPORT, {"export_id": export_id, "learner_id": learner_id})
    if row is None:
        asked = await resources.db.fetch_one(
            _REQUESTED, {"learner_id": learner_id, "export_id": str(export_id)}
        )
        if asked is None:
            raise ApiError("NOT_FOUND", f"export {export_id} not found", status_code=404)
        return ExportStatus(
            export_id=export_id, status="pending", download_url=None, expires_in_s=None,
            expires_at=None,
        )  # fmt: skip
    expires_at: datetime | None = row["expires_at"]
    left = None if expires_at is None else int((expires_at - datetime.now(UTC)).total_seconds())
    if row["status"] != "ready" or row["object_key"] is None or left is None or left <= 0:
        return ExportStatus(
            export_id=export_id, status="expired", download_url=None, expires_in_s=None,
            expires_at=expires_at,
        )  # fmt: skip
    url = await resources.storage.presigned_get_url(row["object_key"], left)
    return ExportStatus(
        export_id=export_id, status="ready", download_url=url, expires_in_s=left,
        expires_at=expires_at,
    )  # fmt: skip
