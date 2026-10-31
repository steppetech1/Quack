"""Request/response models of the Stream 3 endpoints (ТЗ §3), prompt output schemas (ТЗ §4)
and the domain export model (ТЗ §2.3 п.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# --- Practice (ТЗ §3, §2.2) ---


class NextTaskRequest(BaseModel):
    topic_id: UUID
    turn_id: UUID
    kind: Literal["open", "math", "code", "choice"] = "math"
    idempotency_key: str


class NextTaskResponse(BaseModel):
    task_id: UUID
    family_id: str
    difficulty: float
    kind: str
    prompt_text: str
    degraded: bool


class HintRequest(BaseModel):
    turn_id: UUID
    idempotency_key: str


class HintResponse(BaseModel):
    task_id: UUID
    hint_level: int
    hint_text: str
    independence_penalty: float
    seq: int


class SubmitRequest(BaseModel):
    turn_id: UUID
    answer: str
    response_time_sec: float | None = None
    idempotency_key: str


class SubmitResponse(BaseModel):
    task_id: UUID
    evaluator: str
    is_correct: bool
    score: float
    independence: float
    feedback: str
    skill_scores: list
    triggered_misconceptions: list
    seq: int


# --- Notes (ТЗ §3, §2.3) ---


class NoteCreateRequest(BaseModel):
    domain_id: UUID
    topic_id: UUID | None = None
    session_id: UUID | None = None
    title: str
    text: str
    idempotency_key: str


class NoteCreateResponse(BaseModel):
    artifact_id: UUID
    version: Literal[1]
    seq: int


class NoteUpdateRequest(BaseModel):
    title: str
    text: str
    idempotency_key: str


class NoteUpdateResponse(BaseModel):
    artifact_id: UUID
    version: int
    seq: int


class NoteDeleteRequest(BaseModel):
    idempotency_key: str


class NoteDeleteResponse(BaseModel):
    artifact_id: UUID
    is_deleted: Literal[True]
    seq: int


# --- Artifacts, cheatsheets, search (ТЗ §3, A13, A14) ---


class ArtifactView(BaseModel):
    """A row of core.artifacts (A14)."""

    artifact_id: UUID
    kind: str
    domain_id: UUID
    topic_id: UUID | None
    session_id: UUID | None
    title: str
    content_md: str
    version: int
    created_at: datetime
    updated_at: datetime


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactView]


class CheatsheetRefreshRequest(BaseModel):
    domain_id: UUID


class CheatsheetRefreshResponse(BaseModel):
    job_id: UUID
    status: Literal["queued"]


class ArtifactSearchHit(BaseModel):
    artifact_id: UUID
    kind: str
    domain_id: UUID
    topic_id: UUID | None
    title: str
    version: int


class EventSearchHit(BaseModel):
    seq: int
    type: str
    session_id: UUID | None
    occurred_at: datetime
    text: str


class SearchResponse(BaseModel):
    artifact_hits: list[ArtifactSearchHit]
    event_hits: list[EventSearchHit]


# --- Export (ТЗ §2.3 п.4, decision 5) ---


class ExportMdResponse(BaseModel):
    content_md: str


class ExportDomain(BaseModel):
    domain_id: UUID
    name: str
    slug: str


class ExportSkill(BaseModel):
    """`pMastery` / `masteryStatus` keep the names of the Neo4j `Skill` properties."""

    skill_id: UUID
    name: str
    pMastery: float
    masteryStatus: str


class ExportTopic(BaseModel):
    topic_id: UUID
    name: str
    skills: list[ExportSkill]
    cheatsheet: ArtifactView | None


class ExportJson(BaseModel):
    domain: ExportDomain
    topics: list[ExportTopic]
    digests: list[ArtifactView]
    notes: list[ArtifactView]


# --- Prompt output schemas (ТЗ §4) ---
# `TaskGenOutput` and `EvaluatorOutput` live in `quack.practice.schemas` (D-C-013).


class ArtifactGenOutput(BaseModel):
    """artifacts@1; `text` is Markdown."""

    title: str
    text: str
