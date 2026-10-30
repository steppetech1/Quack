"""Pydantic schemas for HTTP requests/responses and LLM structured outputs of the domains
stream (ТЗ §3, §4; spec §5.2; Т-§3.1–3.3, Т-§8.6, Т-§10, Т-§16, Т-§19, Т-§22.2).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from quack.events.payloads import (
    MisconceptionDraft,
    PrereqEdge,
    SkillDepEdge,
    SkillDraft,
    SkillPatch,
    TopicDraft,
    TopicPatch,
    TopicPriorItem,
    Unit,
)
from quack.graph.topic_display import DisplayState

__all__ = [
    "AnswerDiagnosticRequest",
    "AnswerDiagnosticResponse",
    "ArchiveDomainRequest",
    "ArchiveDomainResponse",
    "ArtifactRefView",
    "AuthResponse",
    "ConfirmStructureRequest",
    "ConfirmStructureResponse",
    "CreateDomainRequest",
    "CreateDomainResponse",
    "CuratorMerge",
    "CuratorOutput",
    "CuratorPromotion",
    "DeleteLearnerResponse",
    "DiagnosticOutput",
    "DiagnosticQuestion",
    "DisplayState",
    "DomainGraphResponse",
    "DomainListResponse",
    "DomainSummary",
    "EditStructureRequest",
    "EditStructureResponse",
    "FinishDiagnosticRequest",
    "FinishDiagnosticResponse",
    "LearnerMeResponse",
    "LoginRequest",
    "MisconceptionDraft",
    "MisconceptionNodeView",
    "PrereqEdge",
    "ProposeStructureRequest",
    "ProposeStructureResponse",
    "RegisterRequest",
    "SkillDepEdge",
    "SkillDraft",
    "SkillNodeView",
    "SkillPatch",
    "StartDiagnosticRequest",
    "StartDiagnosticResponse",
    "StructurerMisconception",
    "StructurerOutput",
    "StructurerPrereq",
    "StructurerSkill",
    "StructurerSkillDep",
    "StructurerTopic",
    "TopicDetailResponse",
    "TopicDraft",
    "TopicNodeView",
    "TopicPatch",
    "TopicPriorItem",
    "Unit",
    "UpdatePreferencesRequest",
    "UpdatePreferencesResponse",
    "UpdateTopicStatusRequest",
    "UpdateTopicStatusResponse",
]


# --- 1. Auth & Learner Profile (Т-§16.3, Т-§19, Т-§22.2) ---


class RegisterRequest(BaseModel):
    email: Annotated[str, Field(min_length=1)]
    password: Annotated[str, Field(min_length=8)]
    display_name: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class LoginRequest(BaseModel):
    email: Annotated[str, Field(min_length=1)]
    password: Annotated[str, Field(min_length=1)]


class AuthResponse(BaseModel):
    token: str
    learner_id: UUID
    email: str
    display_name: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)


class LearnerMeResponse(BaseModel):
    learner_id: UUID
    email: str
    display_name: str | None = None
    preferences: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class UpdatePreferencesRequest(BaseModel):
    preferences: dict[str, Any]
    idempotency_key: Annotated[str, Field(min_length=1)]


class UpdatePreferencesResponse(BaseModel):
    preferences: dict[str, Any]
    seq: int


class DeleteLearnerResponse(BaseModel):
    status: Literal["pending_purge"] = "pending_purge"
    requested_at: datetime


# --- 2. Domains CRUD (Т-§16.1) ---


class CreateDomainRequest(BaseModel):
    name: Annotated[str, Field(min_length=1)]
    slug: str | None = None
    idempotency_key: Annotated[str, Field(min_length=1)]


class CreateDomainResponse(BaseModel):
    domain_id: UUID
    name: str
    slug: str
    status: str
    seq: int


class DomainSummary(BaseModel):
    domain_id: UUID
    name: str
    slug: str
    status: str
    has_materials: bool = False
    active_structure_version: int = 0
    created_seq: int = 0
    updated_seq: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None


class DomainListResponse(BaseModel):
    domains: list[DomainSummary]


class ArchiveDomainRequest(BaseModel):
    reason: str | None = None
    idempotency_key: Annotated[str, Field(min_length=1)]


class ArchiveDomainResponse(BaseModel):
    domain_id: UUID
    status: Literal["archived"] = "archived"
    seq: int


# --- 3. Structure Onboarding & Editing (Т-§3.1, Т-§3.2, Т-§16.2) ---


class ProposeStructureRequest(BaseModel):
    goals: str
    background: str
    focus_areas: list[str]
    idempotency_key: Annotated[str, Field(min_length=1)]


class ProposeStructureResponse(BaseModel):
    domain_id: UUID
    structure_version: int
    topics: list[TopicDraft]
    skills: list[SkillDraft]
    prereqs: list[PrereqEdge]
    skill_deps: list[SkillDepEdge]
    misconceptions: list[MisconceptionDraft] = Field(default_factory=list)
    degraded: bool = False


class ConfirmStructureRequest(BaseModel):
    structure_version: int
    idempotency_key: Annotated[str, Field(min_length=1)]


class ConfirmStructureResponse(BaseModel):
    domain_id: UUID
    status: Literal["active"] = "active"
    active_structure_version: int
    seq: int


class EditStructureRequest(BaseModel):
    added_topics: list[TopicDraft] = Field(default_factory=list)
    updated_topics: list[TopicPatch] = Field(default_factory=list)
    archived_topic_ids: list[UUID] = Field(default_factory=list)
    added_prereqs: list[PrereqEdge] = Field(default_factory=list)
    removed_prereqs: list[PrereqEdge] = Field(default_factory=list)
    added_skills: list[SkillDraft] = Field(default_factory=list)
    updated_skills: list[SkillPatch] = Field(default_factory=list)
    archived_skill_ids: list[UUID] = Field(default_factory=list)
    idempotency_key: Annotated[str, Field(min_length=1)]


class EditStructureResponse(BaseModel):
    domain_id: UUID
    active_structure_version: int
    seq: int


class UpdateTopicStatusRequest(BaseModel):
    status: Literal["active", "skipped", "archived"]
    reason: str | None = None
    idempotency_key: Annotated[str, Field(min_length=1)]


class UpdateTopicStatusResponse(BaseModel):
    topic_id: UUID
    status: str
    seq: int


# --- 4. Adaptive Diagnostics (Т-§3.3) ---


class DiagnosticQuestion(BaseModel):
    task_id: UUID
    topic_id: UUID
    skill_id: UUID
    kind: Literal["open", "math", "code", "choice"] = "choice"
    prompt_text: str
    options: list[str] = Field(default_factory=list)


class StartDiagnosticRequest(BaseModel):
    idempotency_key: Annotated[str, Field(min_length=1)]


class StartDiagnosticResponse(BaseModel):
    session_id: UUID
    candidate_topic_ids: list[UUID]
    next_question: DiagnosticQuestion


class AnswerDiagnosticRequest(BaseModel):
    session_id: UUID
    task_id: UUID
    answer: str
    confidence_self_report: Unit | None = None
    response_time_sec: float | None = None
    idempotency_key: Annotated[str, Field(min_length=1)]


class AnswerDiagnosticResponse(BaseModel):
    is_correct: bool
    score: float
    next_question: DiagnosticQuestion | None = None
    should_finish: bool


class FinishDiagnosticRequest(BaseModel):
    session_id: UUID
    idempotency_key: Annotated[str, Field(min_length=1)]


class FinishDiagnosticResponse(BaseModel):
    tested_topic_ids: list[UUID]
    inferred_priors: list[TopicPriorItem]
    seq: int


# --- 5. Knowledge Map Views for UI (Т-§8.6, Т-§22.2) ---


class TopicNodeView(BaseModel):
    topic_id: UUID
    domain_id: UUID
    name: str
    description: str = ""
    status: str
    mastery: float
    coverage: float
    confidence: float
    hub_score: float
    nearest_review_at: int
    display_state: DisplayState
    skill_count: int


class SkillNodeView(BaseModel):
    skill_id: UUID
    topic_id: UUID
    name: str
    description: str = ""
    status: str
    origin: str = "structure"
    p_mastery: float
    effective_mastery: float
    confidence: float
    mastery_status: str
    is_rusty: bool
    evidence_count: int
    distinct_families: int
    stability: float
    last_practiced_at: int
    last_reviewed_at: int
    next_review_at: int
    prior_mastery: float = 0.0
    prior_confidence: float = 0.0
    prior_source: str = "none"


class MisconceptionNodeView(BaseModel):
    misconception_id: UUID
    skill_id: UUID
    name: str
    description: str = ""
    status: str
    occurrences: int
    last_seen_at: int
    resist_count: int


class ArtifactRefView(BaseModel):
    artifact_id: UUID
    kind: str
    title: str
    version: int = 1


class DomainGraphResponse(BaseModel):
    domain_id: UUID
    applied_seq: int
    lag_degraded: bool
    topics: list[TopicNodeView]
    prereqs: list[PrereqEdge]


class TopicDetailResponse(BaseModel):
    topic: TopicNodeView
    skills: list[SkillNodeView]
    skill_deps: list[SkillDepEdge]
    misconceptions: list[MisconceptionNodeView]
    artifacts: list[ArtifactRefView]


# --- 6. Prompt Structured Output Schemas (ТЗ §4) ---


class StructurerTopic(BaseModel):
    slug: str
    name: str
    description: str = ""


class StructurerSkill(BaseModel):
    slug: str
    topic_slug: str
    name: str
    description: str = ""


class StructurerPrereq(BaseModel):
    from_topic_slug: str
    to_topic_slug: str


class StructurerSkillDep(BaseModel):
    skill_slug: str
    depends_on_skill_slug: str


class StructurerMisconception(BaseModel):
    skill_slug: str
    name: str
    description: str = ""


class StructurerOutput(BaseModel):
    topics: list[StructurerTopic] = Field(default_factory=list)
    skills: list[StructurerSkill] = Field(default_factory=list)
    prereqs: list[StructurerPrereq] = Field(default_factory=list)
    skill_deps: list[StructurerSkillDep] = Field(default_factory=list)
    misconceptions: list[StructurerMisconception] = Field(default_factory=list)


class DiagnosticOutput(BaseModel):
    prompt_text: str = ""
    options: list[str] = Field(default_factory=list)
    correct_answer: str = ""
    is_correct: bool = False
    score: Unit = 0.0
    tested_depth: Unit = 0.5


class CuratorPromotion(BaseModel):
    skill_id: UUID
    topic_id: UUID
    name: str
    description: str = ""


class CuratorMerge(BaseModel):
    source_id: UUID
    target_id: UUID
    reason: str


class CuratorOutput(BaseModel):
    promotions: list[CuratorPromotion] = Field(default_factory=list)
    skill_merges: list[CuratorMerge] = Field(default_factory=list)
    topic_merges: list[CuratorMerge] = Field(default_factory=list)
    misconception_merges: list[CuratorMerge] = Field(default_factory=list)
