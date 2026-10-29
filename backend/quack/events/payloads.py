"""Pydantic payload models of the 36 hackathon event types (spec §5.2), schema_version = 1."""

from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Unit = Annotated[float, Field(ge=0.0, le=1.0)]


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- Shared fragments ---


class NewSkillRef(Payload):
    name: Annotated[str, Field(min_length=1)]
    topic_id: UUID


class SkillRef(Payload):
    """Either an existing `skill_id` or an inline provisional `new_skill` (spec §5.1, Т-§4.4)."""

    skill_id: UUID | None = None
    new_skill: NewSkillRef | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> SkillRef:
        if (self.skill_id is None) == (self.new_skill is None):
            raise ValueError("SkillRef requires exactly one of skill_id or new_skill")
        return self


class TopicDraft(Payload):
    topic_id: UUID
    name: Annotated[str, Field(min_length=1)]
    description: str | None = None


class TopicPatch(Payload):
    topic_id: UUID
    name: str | None = None
    description: str | None = None


class SkillDraft(Payload):
    skill_id: UUID
    topic_id: UUID
    name: Annotated[str, Field(min_length=1)]
    description: str | None = None


class SkillPatch(Payload):
    skill_id: UUID
    topic_id: UUID | None = None
    name: str | None = None
    description: str | None = None


class PrereqEdge(Payload):
    """`from_topic_id` is a prerequisite of `to_topic_id`: (from)-[:PREREQ_OF]->(to)."""

    from_topic_id: UUID
    to_topic_id: UUID


class SkillDepEdge(Payload):
    """`skill_id` depends on `depends_on_skill_id`: (skill)-[:DEPENDS_ON]->(dependency)."""

    skill_id: UUID
    depends_on_skill_id: UUID


class MisconceptionDraft(Payload):
    misconception_id: UUID
    skill_id: UUID
    name: Annotated[str, Field(min_length=1)]
    description: str | None = None


class TopicPriorItem(Payload):
    topic_id: UUID
    skill_id: UUID
    prior_mastery: Unit
    confidence: Unit
    source: Literal["tested", "propagated", "self_report"]


class ExtractedObservation(Payload):
    skill: SkillRef
    direction: Literal["positive", "negative", "neutral"]
    strength: Unit
    independence: Unit
    tested_depth: Unit
    polarity_weight: Unit
    evidence_text: str


class ExtractedMisconception(Payload):
    name: Annotated[str, Field(min_length=1)]
    skill: SkillRef
    topic_id: UUID
    confidence: Unit
    evidence_text: str


class TaskSkillTarget(Payload):
    skill: SkillRef
    weight: Unit


class SkillEvalScore(Payload):
    skill_id: UUID
    score: Unit
    tested_depth: Unit


class MisconceptionEvalHit(Payload):
    """A misconception triggered by an answer; referenced by id or by name on `skill_id`."""

    skill_id: UUID
    name: Annotated[str, Field(min_length=1)]
    misconception_id: UUID | None = None


# --- 1. Learner and domains (4) ---


class LearnerRegistered(Payload):
    email: str
    display_name: str | None
    preferences: dict[str, Any]


class LearnerPreferencesUpdated(Payload):
    preferences: dict[str, Any]


class DomainCreated(Payload):
    domain_id: UUID
    name: str
    slug: str
    has_materials: bool = False


class DomainArchived(Payload):
    domain_id: UUID
    reason: str | None = None


# --- 2. Structure (8) ---


class StructureInterviewCompleted(Payload):
    domain_id: UUID
    goals: str
    background: str
    focus_areas: list[str]


class StructureProposed(Payload):
    domain_id: UUID
    structure_version: int
    topics: list[TopicDraft]
    skills: list[SkillDraft]
    prereqs: list[PrereqEdge]
    skill_deps: list[SkillDepEdge]
    misconceptions: list[MisconceptionDraft] = []


class StructureConfirmed(Payload):
    domain_id: UUID
    structure_version: int


class StructureEdited(Payload):
    domain_id: UUID
    structure_version: int
    added_topics: list[TopicDraft] = []
    updated_topics: list[TopicPatch] = []
    archived_topic_ids: list[UUID] = []
    added_prereqs: list[PrereqEdge] = []
    removed_prereqs: list[PrereqEdge] = []
    added_skills: list[SkillDraft] = []
    updated_skills: list[SkillPatch] = []
    archived_skill_ids: list[UUID] = []


class TopicStatusChanged(Payload):
    domain_id: UUID
    topic_id: UUID
    status: Literal["active", "skipped", "archived"]
    reason: str | None = None


class TopicMerged(Payload):
    domain_id: UUID
    source_topic_id: UUID
    target_topic_id: UUID
    reason: str


class SkillPromoted(Payload):
    domain_id: UUID
    skill_id: UUID
    topic_id: UUID
    name: str
    description: str | None = None


class SkillMerged(Payload):
    domain_id: UUID
    source_skill_id: UUID
    target_skill_id: UUID
    reason: str


# --- 3. Diagnostics (3) ---


class DiagnosticStarted(Payload):
    domain_id: UUID
    session_id: UUID
    candidate_topic_ids: list[UUID]


class DiagnosticQuestionAnswered(Payload):
    domain_id: UUID
    session_id: UUID
    turn_id: UUID
    task_id: UUID
    topic_id: UUID
    skill_id: UUID
    answer: str
    is_correct: bool
    score: Unit
    confidence_self_report: Unit | None = None
    response_time_sec: float | None = None
    tested_depth: Unit = 0.5


class DiagnosticCompleted(Payload):
    domain_id: UUID
    session_id: UUID
    tested_topic_ids: list[UUID]
    inferred_priors: list[TopicPriorItem]


# --- 4. Sessions and turns (8) ---


class SessionStarted(Payload):
    session_id: UUID
    domain_id: UUID
    topic_id: UUID | None
    mode: Literal["learn", "practice", "review"]
    goal: str | None
    plan: list[dict[str, Any]]


class SessionCompleted(Payload):
    session_id: UUID
    domain_id: UUID
    summary_stats: dict[str, Any]


class SessionAbandoned(Payload):
    session_id: UUID
    domain_id: UUID
    reason: Literal["inactivity_timeout", "superseded_by_new_session"]


class SessionCheckpointed(Payload):
    session_id: UUID
    up_to_turn_index: int
    rolling_summary: str
    open_threads: list[str]
    key_observations: list[str]


class TurnStudentReplied(Payload):
    session_id: UUID
    turn_id: UUID
    turn_index: int
    text: str
    related_task_id: UUID | None = None


class TurnTeacherReplied(Payload):
    session_id: UUID
    turn_id: UUID
    turn_index: int
    text: str
    pedagogical_move: str
    focus_skill_ids: list[UUID] = []
    degraded_mode: str | None = None


class TeacherConceptExplained(Payload):
    session_id: UUID
    turn_id: UUID
    topic_id: UUID
    skills: list[SkillRef]
    depth: Unit
    summary: str


class TurnExtracted(Payload):
    session_id: UUID
    turn_id: UUID
    observations: list[ExtractedObservation]
    misconceptions: list[ExtractedMisconception] = []


# --- 5. Practice and tasks (5) ---


class TaskGenerated(Payload):
    task_id: UUID
    domain_id: UUID
    topic_id: UUID
    family_id: Annotated[str, Field(min_length=1)]
    difficulty: float
    kind: Literal["open", "math", "code", "choice"]
    prompt_text: str
    reference_solution: dict[str, Any]
    target_skills: list[TaskSkillTarget]
    target_misconceptions: list[str] = []


class TaskAssigned(Payload):
    session_id: UUID
    turn_id: UUID
    task_id: UUID
    topic_id: UUID


class TaskHintGiven(Payload):
    session_id: UUID
    turn_id: UUID
    task_id: UUID
    hint_level: Annotated[int, Field(ge=1, le=3)]
    hint_text: str
    independence_penalty: Unit


class TaskSubmitted(Payload):
    session_id: UUID
    turn_id: UUID
    task_id: UUID
    answer: str
    hints_used: Annotated[int, Field(ge=0)]
    response_time_sec: float | None = None


class TaskEvaluated(Payload):
    session_id: UUID
    turn_id: UUID
    task_id: UUID
    evaluator: Literal["compute", "llm", "hybrid"]
    is_correct: bool
    score: Unit
    independence: Unit
    feedback: str
    skill_scores: list[SkillEvalScore]
    triggered_misconceptions: list[MisconceptionEvalHit] = []


# --- 6. Misconceptions and trace revision (3) ---


class MisconceptionResolved(Payload):
    domain_id: UUID
    misconception_id: UUID
    resolution_reason: str


class MisconceptionMerged(Payload):
    domain_id: UUID
    source_misconception_id: UUID
    target_misconception_id: UUID
    reason: str


class EvidenceSuperseded(Payload):
    target_event_seq: Annotated[int, Field(ge=1)]
    evidence_ids: list[UUID] = []
    reason: str
    replacement_seq: int | None = None


# --- 7. Artifacts (5) ---


class SessionDigestCreated(Payload):
    artifact_id: UUID
    session_id: UUID
    domain_id: UUID
    topic_id: UUID | None
    title: str
    text: str
    covered_skill_ids: list[UUID]


class ArtifactNoteCreated(Payload):
    artifact_id: UUID
    domain_id: UUID
    topic_id: UUID | None
    session_id: UUID | None
    title: str
    text: str


class ArtifactNoteUpdated(Payload):
    artifact_id: UUID
    version: Annotated[int, Field(ge=1)]
    title: str
    text: str


class ArtifactNoteDeleted(Payload):
    artifact_id: UUID


class ArtifactCheatsheetUpdated(Payload):
    artifact_id: UUID
    domain_id: UUID
    topic_id: UUID
    version: Annotated[int, Field(ge=1)]
    title: str
    text: str
