"""Event catalog of Phase 4 (Т-§2.2; ТЗ Ф4 §6.3, WP2.1, seam S3). All types `schema_version = 1`.

Fields: named in §6.3 and the owning sections of Т-§ (snake_case, Т-§0.4); the rest is the
minimum the reading rule needs (§0.4.3 «частые случаи», row 1). Stream B's payload shapes come
from its models (PR #72, Q-A-1): `quack.events.values` and `quack.events.records`. Not
registered (ПОЗЖЕ or outside the slice, §6.3): `domain.assigned`, `hub.suggested`,
`hub.accepted`, `topic.moved`, shadow chunk events. Draft at G0, frozen at G1 (S3).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from quack.events.records import (
    AgentKind,
    AgentParamsRecord,
    BodyBlockRecord,
    DiagnosisRecord,
    DigestStructuredRecord,
    ExtractedElementRecord,
    RejectedElementRecord,
    TaskMarkupRecord,
    TurnHeadRecord,
    TurnMarkupRecord,
)
from quack.events.registry import EventPayload, register
from quack.events.values import (
    CheckFailed,
    Confidence,
    LearnerAnswer,
    Level,
    Verdict,
)

type Unit = Annotated[float, Field(ge=0.0, le=1.0)]
# Т-§3.4.2 Topic.origin.
type Origin = Literal["session", "material", "learner"]
# ТЗ Ф4 §4.4 (Ф3-§4.4): one value per turn, by priority.
type DegradedMode = Literal[
    "budget_exceeded", "llm_unavailable", "memory_unavailable", "teacher_fallback", "projector_lag"
]


# --- learner actions ---------------------------------------------------------------------


class CanvasRef(EventPayload):
    canvas_id: UUID
    line_id: str | None = None
    block_id: str | None = None


@register("message.sent", projector=False, dispatcher=True)
class MessageSent(EventPayload):
    text: str
    intent: Literal["learn", "practice", "review"] | None = None
    topic_id: UUID | None = None
    hub_id: UUID | None = None
    material_ids: list[UUID] = Field(default_factory=list)
    canvas_refs: list[CanvasRef] = Field(default_factory=list)
    skill_invocation: str | None = None


@register("answer.submitted", projector=True, dispatcher=True)
class AnswerSubmitted(EventPayload):
    """`text` — the learner's reasoning; card self-assessment is yes/no (D-B-112)."""

    task_id: UUID
    part: str | None = None
    answer: LearnerAnswer
    text: str | None = None
    confidence: Confidence | None = None
    element_id: str | None = None
    source: Literal["chat", "canvas", "photo", "card"]
    self_assessment: bool | None = None
    recognition_confidence: Unit | None = None
    solve_time_ms: int | None = None


@register("element.responded", projector=False, dispatcher=True)
class ElementResponded(EventPayload):
    turn_seq: int
    element_id: str
    response: str
    text: str | None = None
    confidence: Confidence | None = None


@register("canvas.line_committed", projector=False, dispatcher=True)
class CanvasLineCommitted(EventPayload):
    canvas_id: UUID
    line_id: str
    text: str
    sympy: str | None = None
    kind: str
    bbox: tuple[float, float, float, float]
    stroke_ids: list[str]
    started_at_ms: int
    ended_at_ms: int
    source: Literal["pen", "typed", "photo"]
    photo_id: UUID | None = None
    recognition_confidence: Unit
    crossed_out: bool


@register("canvas.line_erased", projector=False, dispatcher=True)
class CanvasLineErased(EventPayload):
    canvas_id: UUID
    line_id: str
    text: str


@register("voice.transcribed", projector=False, dispatcher=True)
class VoiceTranscribed(EventPayload):
    text: str
    started_at_ms: int
    ended_at_ms: int


@register("photo.uploaded", projector=False, dispatcher=True)
class PhotoUploaded(EventPayload):
    canvas_id: UUID | None = None
    photo_id: UUID
    object_key: str


@register("confidence.reported", projector=False, dispatcher=False)
class ConfidenceReported(EventPayload):
    target_seq: int
    value: Confidence


class _TaskAction(EventPayload):
    task_id: UUID


@register("hint.requested", projector=False, dispatcher=True)
class HintRequested(_TaskAction):
    pass


@register("solution.requested", projector=False, dispatcher=True)
class SolutionRequested(_TaskAction):
    pass


@register("check.skipped", projector=False, dispatcher=True)
class CheckSkipped(_TaskAction):
    pass


@register("item.flagged_unclear", projector=False, dispatcher=True)
class ItemFlaggedUnclear(EventPayload):
    """§6.3 names the quoted fragment `quote`; Т-§3.1 requires searchable text in `text`
    (WP2.3 lists this type) — the fragment is `text` (D-A-107)."""

    turn_seq: int
    block_id: str
    text: str


class _HubRef(EventPayload):
    hub_id: UUID


@register("prep_mode.enabled", projector=False, dispatcher=False)
class PrepModeEnabled(_HubRef):
    pass


@register("prep_mode.disabled", projector=False, dispatcher=False)
class PrepModeDisabled(_HubRef):
    pass


@register("grade.reported", projector=False, dispatcher=False)
class GradeReported(EventPayload):
    hub_id: UUID
    grade: str
    scale: str


# --- teacher turn ------------------------------------------------------------------------


@register("turn.committed", projector=True, dispatcher=True)
class TurnCommitted(EventPayload):
    """Т-§12.2: the turn text and markup with ids; side effects go in the same append (WP2.2)."""

    action_seq: int
    text: str
    blocks: list[BodyBlockRecord]
    head: TurnHeadRecord
    markup: TurnMarkupRecord
    role: str
    model: str
    effort: str
    degraded_mode: DegradedMode | None
    guard_retries: int = Field(ge=0)
    manifest_id: UUID


# --- verdicts ----------------------------------------------------------------------------


# The shared models are the payloads themselves, so stream B writes its objects directly.
register("verdict.recorded", projector=True, dispatcher=True)(Verdict)
register("check.failed", projector=False, dispatcher=True)(CheckFailed)


# --- observations ------------------------------------------------------------------------


@register("observation.extracted", projector=True, dispatcher=True)
class ObservationExtracted(EventPayload):
    episode_seq: int
    extractor: str
    trigger_seq: int
    elements: list[ExtractedElementRecord]
    rejected: list[RejectedElementRecord] = Field(default_factory=list)


@register("observation.diagnosed", projector=True, dispatcher=True)
class ObservationDiagnosed(EventPayload):
    canvas_id: UUID
    line_id: str
    task_id: UUID | None
    diagnosis: DiagnosisRecord
    confidence: Unit
    method: Literal["model", "symbolic"]


@register("observation.second_opinion", projector=True, dispatcher=False)
class ObservationSecondOpinion(EventPayload):
    target_seq: int
    agreed: bool
    diagnosis: DiagnosisRecord | None = None


# --- subject structure (Т-§4) ------------------------------------------------------------


@register("domain.created", projector=True, dispatcher=False)
class DomainCreated(EventPayload):
    domain_id: UUID
    name: str


class _TopicNew(EventPayload):
    topic_id: UUID
    domain_id: UUID
    name: str
    origin: Origin


@register("topic.proposed", projector=True, dispatcher=True)
class TopicProposed(_TopicNew):
    pass


@register("topic.created", projector=True, dispatcher=True)
class TopicCreated(_TopicNew):
    pass


@register("topic.merged", projector=True, dispatcher=False)
class TopicMerged(EventPayload):
    from_id: UUID
    into_id: UUID


class TopicPart(EventPayload):
    topic_id: UUID
    name: str
    skill_ids: list[UUID]


@register("topic.split", projector=True, dispatcher=False)
class TopicSplit(EventPayload):
    topic_id: UUID
    into: list[TopicPart] = Field(min_length=2)


@register("topic.renamed", projector=True, dispatcher=False)
class TopicRenamed(EventPayload):
    topic_id: UUID
    name: str


@register("topic.hidden", projector=True, dispatcher=False)
class TopicHidden(EventPayload):
    topic_id: UUID


@register("skill.created", projector=True, dispatcher=True)
class SkillCreated(EventPayload):
    skill_id: UUID
    topic_id: UUID
    domain_id: UUID
    name: str
    summary: str
    status: Literal["provisional", "active"]
    origin: Origin


@register("skill.merged", projector=True, dispatcher=True)
class SkillMerged(EventPayload):
    from_id: UUID
    into_id: UUID


class SkillPart(EventPayload):
    skill_id: UUID
    name: str
    topic_id: UUID


@register("skill.split", projector=True, dispatcher=True)
class SkillSplit(EventPayload):
    skill_id: UUID
    into: list[SkillPart] = Field(min_length=2)


@register("skill.merge_suggested", projector=False, dispatcher=False)
class SkillMergeSuggested(EventPayload):
    from_id: UUID
    into_id: UUID


class _Edge(EventPayload):
    kind: Literal["requires", "same_idea"]
    from_id: UUID
    to_id: UUID


@register("edge.added", projector=True, dispatcher=False)
class EdgeAdded(_Edge):
    origin: Literal["material", "model", "learner"]  # Т-§4.6
    confidence: Unit


@register("edge.removed", projector=True, dispatcher=False)
class EdgeRemoved(_Edge):
    pass


@register("hub.created", projector=True, dispatcher=False)
class HubCreated(EventPayload):
    hub_id: UUID
    domain_id: UUID
    name: str
    kind: Literal["material", "request", "domain_all"]
    origin: Origin
    control_type: Literal["tasks", "colloquium_proofs", "oral_theory", "no_goal"]


@register("hub.deadline_set", projector=True, dispatcher=True)
class HubDeadlineSet(EventPayload):
    """`deadline_at_ms = None` removes the deadline (WP6.3 «снятием дедлайна»)."""

    hub_id: UUID
    deadline_at_ms: int | None
    source: Literal["turn", "observation", "syllabus", "learner"]


class HubTopic(EventPayload):
    topic_id: UUID
    required_levels: list[Level]


@register("hub.topics_changed", projector=True, dispatcher=False)
class HubTopicsChanged(EventPayload):
    hub_id: UUID
    topics: list[HubTopic]


# --- tasks (Т-§5) ------------------------------------------------------------------------


@register("task.created", projector=True, dispatcher=True)
class TaskCreated(EventPayload):
    task_id: UUID
    family_id: UUID
    source: Literal["seminar", "generated", "material", "teacher"]
    markup: TaskMarkupRecord
    verification: Literal["unchecked", "checked", "disputed"]


class _TaskCheckResult(EventPayload):
    task_id: UUID
    corrected_markup: TaskMarkupRecord | None = None


@register("task.verified", projector=True, dispatcher=False)
class TaskVerified(_TaskCheckResult):
    pass


@register("task.key_disputed", projector=True, dispatcher=False)
class TaskKeyDisputed(_TaskCheckResult):
    pass


@register("task.markup_disputed", projector=True, dispatcher=False)
class TaskMarkupDisputed(_TaskCheckResult):
    pass


# --- materials ---------------------------------------------------------------------------


@register("material.uploaded", projector=True, dispatcher=True)
class MaterialUploaded(EventPayload):
    material_id: UUID
    object_key: str
    mime: str
    session_id: UUID | None = None


@register("material.parsed", projector=True, dispatcher=True)
class MaterialParsed(EventPayload):
    material_id: UUID
    pages_count: int = Field(ge=0)


@register("chunk.indexed", projector=True, dispatcher=True)
class ChunkIndexed(EventPayload):
    chunk_id: UUID
    material_id: UUID
    page: int
    text: str
    notation: list[str]
    teaches: list[UUID]
    uses: list[UUID]


# --- learner edits (Т-§2.8, Т-§4.11, Т-§9.5, Т-§19.4) ------------------------------------


@register("dispute.filed", projector=True, dispatcher=False)
class DisputeFiled(EventPayload):
    target_kind: Literal["evidence", "misconception", "verdict"]
    target_id: str


@register("typo.declared", projector=True, dispatcher=False)
class TypoDeclared(EventPayload):
    answer_seq: int
    part: str


@register("known.claimed", projector=True, dispatcher=True)
class KnownClaimed(EventPayload):
    topic_id: UUID


@register("handwriting.corrected", projector=False, dispatcher=True)
class HandwritingCorrected(EventPayload):
    canvas_id: UUID
    line_id: str
    text: str
    sympy: str | None = None


@register("episode.reassigned", projector=False, dispatcher=True)
class EpisodeReassigned(EventPayload):
    episode_seq: int
    topic_id: UUID


@register("preference.set", projector=True, dispatcher=False)
class PreferenceSet(EventPayload):
    key: str
    value: str | int | float | bool


class _Consent(EventPayload):
    kind: Literal["terms", "research"]
    version: str


@register("consent.given", projector=False, dispatcher=False)
class ConsentGiven(_Consent):
    pass


@register("consent.revoked", projector=False, dispatcher=False)
class ConsentRevoked(_Consent):
    pass


# --- session flow ------------------------------------------------------------------------


class _SessionRef(EventPayload):
    session_id: UUID
    domain_id: UUID
    intent: str
    topic_id: UUID | None = None
    hub_id: UUID | None = None


@register("session.started", projector=False, dispatcher=True)
class SessionStarted(_SessionRef):
    pass


@register("session.ended", projector=True, dispatcher=True)
class SessionEnded(EventPayload):
    session_id: UUID
    reason: str


@register("hint.shown", projector=False, dispatcher=True)
class HintShown(EventPayload):
    task_id: UUID
    step: int = Field(ge=1, le=3)
    source: Literal["teacher", "canvas"]


class TodayItem(EventPayload):
    """A review candidate shown in «Сегодня» (WP17.1–17.2; the shape is fixed in WP17)."""

    skill_id: UUID
    topic_id: UUID | None = None
    hub_id: UUID | None = None
    reasons: list[Literal["due", "consolidation", "misconception", "deadline"]]
    score: float


@register("today.shown", projector=False, dispatcher=True)
class TodayShown(EventPayload):
    domain_id: UUID
    items: list[TodayItem] = Field(min_length=1)


@register("task.closed", projector=True, dispatcher=True)
class TaskClosed(EventPayload):
    task_id: UUID
    how: Literal["solved", "skipped", "solution_shown"]


@register("episode.closed", projector=False, dispatcher=True)
class EpisodeClosed(EventPayload):
    from_seq: int
    to_seq: int
    has_content: bool


# --- service -----------------------------------------------------------------------------


@register("agent.requested", projector=False, dispatcher=True)
class AgentRequested(EventPayload):
    request_id: UUID
    kind: AgentKind
    params: AgentParamsRecord
    needed_in_session: bool


@register("agent.completed", projector=False, dispatcher=False)
class AgentCompleted(EventPayload):
    """`request_id` for requests, `job_key` for jobs (Т-§13.9); `task_ids` — WP8.2 known check."""

    request_id: UUID | None = None
    job_key: str | None = None
    kind: str
    ok: bool
    result_ref: str | None = None
    error: str | None = None
    task_ids: list[UUID] = Field(default_factory=list)


@register("digest.written", projector=True, dispatcher=True)
class DigestWritten(EventPayload):
    """`manner` — the teacher manner text of the profile (ТЗ Ф4 WP5.3)."""

    domain_id: UUID
    text: str
    as_of_seq: int
    structured: DigestStructuredRecord
    plan_divergence: bool
    manner: str


@register("misconception.described", projector=True, dispatcher=True)
class MisconceptionDescribed(EventPayload):
    misconception_id: UUID
    statement: str
    triggers: list[str]


@register("misconception.merged", projector=True, dispatcher=False)
class MisconceptionMerged(EventPayload):
    from_id: UUID
    into_id: UUID


@register("artifact.updated", projector=False, dispatcher=False)
class ArtifactUpdated(EventPayload):
    artifact_id: UUID
    version: int = Field(ge=1)
    kind: str
    topic_id: UUID | None = None
    hub_id: UUID | None = None
    text: str


@register("embedding.ready", projector=True, dispatcher=False)
class EmbeddingReady(EventPayload):
    """Т-§3.1: the vector in binary form (base64), not a JSON array of numbers."""

    node_kind: Literal["evidence", "misconception", "chunk", "skill", "topic"]
    node_id: UUID
    model: str
    dim: int = Field(ge=1)
    vector: str
