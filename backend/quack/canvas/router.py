"""Routes of canvases (ТЗ Ф4 §6.4 «Канвас», WP21.1, WP21.2): `POST /v1/sessions/{id}/canvases`,
`GET /v1/canvases/{id}`, `GET /v1/domains/{id}/canvases`, `POST /v1/canvases/{id}/photos`,
`POST /v1/canvases/{id}/lines/{line_id}/correct`, `WS /v1/canvases/{id}/ws`,
`GET /v1/canvases/{id}/replay` and `POST /v1/sessions/{id}/voice` (WP21.6). The
learner comes only from the session cookie (WP18.3); a session, task, canvas or domain of another
learner is 404."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, WebSocket
from pydantic import BaseModel, Field

from quack.api.auth import (
    CurrentSession,
    get_any_session,
    get_current_session,
    websocket_session,
)
from quack.api.errors import ApiError
from quack.api.resources import AppResources, get_resources
from quack.canvas import boards, photos, recognition, replay, socket, voice
from quack.canvas.board import BoardDoc
from quack.storage.uploads import check_upload_size

router = APIRouter(prefix="/v1", tags=["canvas"])

Resources = Annotated[AppResources, Depends(get_resources)]
Session = Annotated[CurrentSession, Depends(get_current_session)]


class OpenCanvas(BaseModel):
    task_id: UUID | None = None


class CanvasView(BaseModel):
    canvas_id: UUID
    session_id: UUID | None
    task_id: UUID | None
    doc: BoardDoc
    version: int
    updated_at: datetime


class LineCorrection(BaseModel):
    """WP21.3: how the line really reads (`Т-§22.7`: LaTeX to show, SymPy to check)."""

    idempotency_key: str = Field(min_length=1)
    text: str = Field(min_length=1)
    sympy: str | None = None


class CorrectedLine(BaseModel):
    canvas_id: UUID
    line_id: str
    text: str
    sympy: str | None


class PhotoView(BaseModel):
    photo_id: UUID
    canvas_id: UUID


class VoiceView(BaseModel):
    """WP21.6: what was heard; `seq` is the `voice.transcribed` event, `null` for silence."""

    text: str
    started_at_ms: int
    ended_at_ms: int
    seq: int | None


class CanvasList(BaseModel):
    canvases: list[CanvasView]


class ReplayChunkView(BaseModel):
    url: str
    size: int


class ReplayView(BaseModel):
    """Signed links to the stroke chunks of the canvas, oldest first (Т-§15.7, D-C-135)."""

    chunks: list[ReplayChunkView]
    expires_in_s: int


def _view(canvas: boards.Canvas) -> CanvasView:
    return CanvasView(
        canvas_id=canvas.canvas_id,
        session_id=canvas.session_id,
        task_id=canvas.task_id,
        doc=canvas.doc,
        version=canvas.version,
        updated_at=canvas.updated_at,
    )


def _not_found(exc: boards.CanvasNotFound) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


@router.post("/sessions/{session_id}/canvases", status_code=201, response_model=CanvasView)
async def open_canvas(
    session_id: UUID, body: OpenCanvas, session: Session, resources: Resources
) -> CanvasView:
    try:
        canvas = await boards.open_canvas(
            resources.db,
            learner_id=session.learner_id,
            session_id=session_id,
            task_id=body.task_id,
            redis=resources.redis,
        )
    except boards.CanvasNotFound as exc:
        raise _not_found(exc) from exc
    return _view(canvas)


@router.get("/canvases/{canvas_id}", response_model=CanvasView)
async def get_canvas(canvas_id: UUID, session: Session, resources: Resources) -> CanvasView:
    try:
        canvas = await boards.get_canvas(
            resources.db, learner_id=session.learner_id, canvas_id=canvas_id
        )
    except boards.CanvasNotFound as exc:
        raise _not_found(exc) from exc
    return _view(canvas)


@router.get("/domains/{domain_id}/canvases", response_model=CanvasList)
async def domain_canvases(domain_id: UUID, session: Session, resources: Resources) -> CanvasList:
    try:
        canvases = await boards.domain_canvases(
            resources.db, learner_id=session.learner_id, domain_id=domain_id
        )
    except boards.CanvasNotFound as exc:
        raise _not_found(exc) from exc
    return CanvasList(canvases=[_view(c) for c in canvases])


@router.get("/canvases/{canvas_id}/replay", response_model=ReplayView)
async def canvas_replay(canvas_id: UUID, session: Session, resources: Resources) -> ReplayView:
    """WP21.6: links live `object_url_ttl_s`; a canvas without a session has no strokes."""
    try:
        canvas = await boards.get_canvas(
            resources.db, learner_id=session.learner_id, canvas_id=canvas_id
        )
    except boards.CanvasNotFound as exc:
        raise _not_found(exc) from exc
    ttl_s = resources.settings.object_url_ttl_s
    chunks: list[replay.ReplayChunk] = []
    if canvas.session_id is not None:
        chunks = await replay.replay_chunks(
            resources.storage,
            learner_id=session.learner_id,
            session_id=canvas.session_id,
            canvas_id=canvas_id,
            ttl_s=ttl_s,
        )
    return ReplayView(
        chunks=[ReplayChunkView(url=c.url, size=c.size) for c in chunks], expires_in_s=ttl_s
    )


@router.post("/canvases/{canvas_id}/photos", status_code=201, response_model=PhotoView)
async def upload_photo(
    canvas_id: UUID,
    session: Annotated[CurrentSession, Depends(get_any_session)],
    resources: Resources,
    file: Annotated[UploadFile, File()],
) -> PhotoView:
    """WP22.2: the laptop (`full`) or the phone paired to this canvas (`companion`, WP22.1);
    a companion of another canvas — 403 (T-CP1)."""
    if not socket.may_open(session, canvas_id):
        raise ApiError("UNAUTHORIZED", "a companion session of another canvas", status_code=403)
    try:
        photo = await photos.upload_photo(
            resources.db,
            resources.storage,
            resources.settings,
            learner_id=session.learner_id,
            canvas_id=canvas_id,
            data=await file.read(),
            redis=resources.redis,
        )
    except boards.CanvasNotFound as exc:
        raise _not_found(exc) from exc
    return PhotoView(photo_id=photo.photo_id, canvas_id=photo.canvas_id)


@router.post("/sessions/{session_id}/voice", status_code=201, response_model=VoiceView)
async def upload_voice(
    session_id: UUID,
    session: Session,
    resources: Resources,
    audio: Annotated[UploadFile, File()],
    started_at_ms: Annotated[int, Form(ge=0)],
    ended_at_ms: Annotated[int, Form(ge=0)],
) -> VoiceView:
    """WP21.6: one segment of speech (WAV) with its start and end by the device clock; the
    audio is not stored (П-14.1). The companion has no voice (T-CP1: 403). A session of another
    learner is 404; compressed audio is 415; the same segment again gives the same answer.
    Larger than `upload_max_mb` is 413 before the body is read (Т-§19.8, as photos)."""
    max_mb = resources.settings.upload_max_mb
    check_upload_size(audio.size, max_mb=max_mb)
    data = await audio.read()
    check_upload_size(len(data), max_mb=max_mb)
    try:
        heard = await voice.transcribe_segment(
            resources.db,
            resources.llm,
            learner_id=session.learner_id,
            session_id=session_id,
            audio=data,
            started_at_ms=started_at_ms,
            ended_at_ms=ended_at_ms,
            redis=resources.redis,
        )
    except voice.SessionNotFound as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    except voice.InvalidSegment as exc:
        raise ApiError("VALIDATION_ERROR", str(exc), status_code=422) from exc
    return VoiceView(
        text=heard.text,
        started_at_ms=heard.started_at_ms,
        ended_at_ms=heard.ended_at_ms,
        seq=heard.seq,
    )


@router.post("/canvases/{canvas_id}/lines/{line_id}/correct", response_model=CorrectedLine)
async def correct_line(
    canvas_id: UUID, line_id: str, body: LineCorrection, session: Session, resources: Resources
) -> CorrectedLine:
    """WP21.3: `handwriting.corrected` (Т-§2.8, П-9.7); a canvas or line of another learner, or
    a line the canvas does not have, is 404."""
    try:
        canvas = await boards.get_canvas(
            resources.db, learner_id=session.learner_id, canvas_id=canvas_id
        )
        corrected = await recognition.correct_line(
            resources.db,
            learner_id=session.learner_id,
            session_id=canvas.session_id,
            canvas_id=canvas_id,
            line_id=line_id,
            text=body.text,
            sympy=body.sympy,
            idempotency_key=body.idempotency_key,
            redis=resources.redis,
        )
    except boards.CanvasNotFound as exc:
        raise _not_found(exc) from exc
    except recognition.LineNotFound as exc:
        raise ApiError("NOT_FOUND", str(exc), status_code=404) from exc
    return CorrectedLine(
        canvas_id=corrected.canvas_id,
        line_id=corrected.line_id,
        text=corrected.text,
        sympy=corrected.sympy,
    )


@router.websocket("/canvases/{canvas_id}/ws")
async def canvas_socket(websocket: WebSocket, canvas_id: UUID) -> None:
    """WP21.2: refused with 1008 without a session that may open this canvas (D-C-121)."""
    resources = get_resources(websocket)
    session = await websocket_session(websocket)
    canvas = None
    if socket.may_open(session, canvas_id):
        assert session is not None
        try:
            canvas = await boards.get_canvas(
                resources.db, learner_id=session.learner_id, canvas_id=canvas_id
            )
        except boards.CanvasNotFound:
            canvas = None
    if session is None or canvas is None or canvas.session_id is None:
        await websocket.close(code=socket.CLOSE_POLICY)
        return
    await websocket.accept()
    await socket.serve_canvas(websocket, resources, session, canvas)
