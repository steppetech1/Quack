"""Routes of materials and full-text search (ТЗ Ф4 §6.4 «Материалы и поиск», WP20.1, WP20.5).

`POST /v1/domains/{id}/materials` (multipart: `file`, optional `session_id`),
`GET /v1/domains/{id}/materials`, `GET /v1/materials/{id}`, `GET /v1/search`. The learner
comes only from the session cookie (WP18.3); a domain or a material of another learner is 404.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from pydantic import BaseModel

from quack.api.auth import CurrentSession, get_current_session
from quack.api.errors import ApiError
from quack.api.resources import AppResources, get_resources
from quack.materials import library
from quack.materials.search import search_events

router = APIRouter(prefix="/v1", tags=["materials"])

Resources = Annotated[AppResources, Depends(get_resources)]
Session = Annotated[CurrentSession, Depends(get_current_session)]


class MaterialView(BaseModel):
    material_id: UUID
    domain_id: UUID | None
    session_id: UUID | None
    mime: str
    status: str
    pages_count: int | None
    created_at: datetime


class MaterialList(BaseModel):
    materials: list[MaterialView]


class MaterialPageView(BaseModel):
    page: int
    text: str
    image_url: str | None


class MaterialDetailView(BaseModel):
    material: MaterialView
    file_url: str
    pages: list[MaterialPageView]


class SearchHitView(BaseModel):
    """WP20.5: the session, `seq`, the snippet and the type of a found event."""

    session_id: UUID | None
    seq: int
    type: str
    snippet: str


class SearchHits(BaseModel):
    hits: list[SearchHitView]


def _not_found(exc: library.MaterialNotFound) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


def _view(material: library.Material) -> MaterialView:
    return MaterialView(
        material_id=material.material_id,
        domain_id=material.domain_id,
        session_id=material.session_id,
        mime=material.mime,
        status=material.status,
        pages_count=material.pages_count,
        created_at=material.created_at,
    )


@router.post("/domains/{domain_id}/materials", status_code=201, response_model=MaterialView)
async def upload_material(
    domain_id: UUID,
    session: Session,
    resources: Resources,
    file: Annotated[UploadFile, File()],
    session_id: Annotated[UUID | None, Form()] = None,
) -> MaterialView:
    try:
        material = await library.upload_material(
            resources.db,
            resources.storage,
            resources.settings,
            learner_id=session.learner_id,
            domain_id=domain_id,
            data=await file.read(),
            session_id=session_id,
            redis=resources.redis,
        )
    except library.MaterialNotFound as exc:
        raise _not_found(exc) from exc
    return _view(material)


@router.get("/domains/{domain_id}/materials", response_model=MaterialList)
async def list_materials(domain_id: UUID, session: Session, resources: Resources) -> MaterialList:
    try:
        materials = await library.list_materials(
            resources.db, learner_id=session.learner_id, domain_id=domain_id
        )
    except library.MaterialNotFound as exc:
        raise _not_found(exc) from exc
    return MaterialList(materials=[_view(m) for m in materials])


@router.get("/materials/{material_id}", response_model=MaterialDetailView)
async def get_material(
    material_id: UUID, session: Session, resources: Resources
) -> MaterialDetailView:
    try:
        detail = await library.get_material(
            resources.db,
            resources.storage,
            resources.settings,
            learner_id=session.learner_id,
            material_id=material_id,
        )
    except library.MaterialNotFound as exc:
        raise _not_found(exc) from exc
    return MaterialDetailView(
        material=_view(detail.material),
        file_url=detail.file_url,
        pages=[
            MaterialPageView(page=p.page, text=p.text, image_url=p.image_url) for p in detail.pages
        ],
    )


@router.get("/search", response_model=SearchHits)
async def search(
    session: Session,
    resources: Resources,
    q: Annotated[str, Query()],
    domain_id: UUID | None = None,
    limit: int | None = None,
) -> SearchHits:
    """D-C-116: the query length and the size of the answer follow the Phase 3 search
    (`search_min_query_len`, `search_default_limit`, `search_max_limit`)."""
    cfg = resources.settings
    if len(q) < cfg.search_min_query_len:
        raise ApiError(
            "VALIDATION_ERROR",
            f"query must be at least {cfg.search_min_query_len} characters",
            status_code=422,
        )
    effective_limit = cfg.search_default_limit if limit is None else limit
    if effective_limit < 1 or effective_limit > cfg.search_max_limit:
        raise ApiError(
            "VALIDATION_ERROR",
            f"limit must be between 1 and {cfg.search_max_limit}",
            status_code=422,
        )
    hits = await search_events(
        resources.db,
        learner_id=session.learner_id,
        query=q,
        domain_id=domain_id,
        limit=effective_limit,
    )
    return SearchHits(
        hits=[
            SearchHitView(session_id=h.session_id, seq=h.seq, type=h.type, snippet=h.snippet)
            for h in hits
        ]
    )
