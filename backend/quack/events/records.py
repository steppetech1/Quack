"""Id forms of model outputs stored in events (ТЗ Ф4 WP2.1, seam S3; Q-A-1 variant (a)).

Models see short labels (`S1`, `M2`, `K3`, `C4`, `R5`, `P6`, `H7`, `T8`; Т-§4.5, D-B-108); code
translates them to ids before the event is written (§6.3 «markup (ids вместо меток)»). The
label-bearing output models stay in stream B (`TaskMarkup`, `TurnMarkup`, `ExtractedElement`,
`Element`, analyst output); here are the same structures with ids. Field names are snake_case
(Т-§0.4). A label field `x` becomes `x_id`; `R…` (agent requests) become `request_id`.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from quack.events.values import (
    AnswerSpec,
    GapType,
    Level,
    MathValue,
    NewSkill,
    TaskForm,
)


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- task markup (Т-§5.2; B: TaskMarkup) -------------------------------------------------


class TestedLevelRecord(_Record):
    """One pair of `tests`: an existing skill or a description of a new one (Т-§4.4)."""

    skill_id: UUID | None = None
    new_skill: NewSkill | None = None
    level: Level


class OptionTrapRecord(_Record):
    """A wrong option catches a known misconception (`misconception_id`) or a hypothesis."""

    option_id: str
    misconception_id: UUID | None = None
    hypothesis: str | None = None
    neighbor_idea: bool = False


class TaskPartRecord(_Record):
    part: str
    answer: AnswerSpec
    tests: list[TestedLevelRecord] | None = None


class TaskMarkupRecord(_Record):
    """Т-§5.2 markup with ids: `variant_of`, `pair_task_id` are task ids."""

    tests: list[TestedLevelRecord]
    method_named: bool
    variant_of: UUID | None = None
    answer: AnswerSpec
    options: list[OptionTrapRecord] | None = None
    pair_task_id: UUID | None = None
    form: TaskForm | None = None
    parts: list[TaskPartRecord] | None = None


# --- teacher turn (Т-§12.2–12.6, WP13.3; B: TeacherOutputV2) -----------------------------

type TurnIntent = Literal[
    "clarify_goal",
    "explain",
    "worked_example",
    "practice",
    "retrieval",
    "diagnose",
    "hint",
    "feedback",
    "answer_question",
    "plan",
    "wrap_up",
    "split_topic",
]
type Approach = Literal[
    "example_first",
    "attempt_first",
    "small_steps",
    "prediction_first",
    "faded_example",
    "interleaving",
    "contrast_pair",
    "refutation",
    "explain_in_own_words",
    "visualization",
    "none",
]
type VerdictStance = Literal["code", "formative"]
type BranchType = Literal["corrective", "curiosity", "external"]
type ExposureKind = Literal["explained", "example", "method_named", "solution_shown"]
type AgentKind = Literal[
    "viz",
    "illustration",
    "task_generation",
    "solution_space",
    "artifact",
    "known_check",
    "topic_split",
    "data_export",
]


class TurnHeadRecord(_Record):
    intent: TurnIntent
    target_skill_ids: list[UUID]
    verdict_stance: VerdictStance | None
    branch_type: BranchType | None
    approach: Approach


class GuardEventRecord(_Record):
    """One firing of a code rule of the teacher turn (Т-§12.4), for the analysis of the guard:
    names only — the rule (`head`, `boundary`, `markup`, `element_markup`, `task_markup`,
    `spoiler`, `incomplete`), the reason (`GuardReason` or a rule of `validate_markup`), the phase
    and whether the firing returned the turn to the model. Never a text, a label or an answer."""

    rule: str
    reason: str
    phase: Literal["head", "markup", "spoiler", "spoiler_shadow", "cut"]
    retried: bool = False


class BodyBlockRecord(_Record):
    block_id: str
    text: str | None
    element_id: str | None


class TurnTaskRecord(TaskMarkupRecord):
    """A task shown as an element; `task_id` — the task created by the turn (`task.created`
    in the same append, ТЗ Ф4 WP2.2)."""

    element_id: str
    task_id: UUID


class _Element(_Record):
    element_id: str


class ChoiceElementRecord(_Element):
    kind: Literal["choice"]
    prompt: str
    ask_confidence: bool


class WhyChoiceElementRecord(_Element):
    kind: Literal["why_choice"]
    prompt: str
    allow_other: bool
    ask_confidence: bool


class ShortAnswerElementRecord(_Element):
    kind: Literal["short_answer"]
    prompt: str
    ask_confidence: bool


class TaskElementRecord(_Element):
    kind: Literal["task"]
    prompt: str
    canvas: bool
    ask_confidence: bool


class ScaffoldGapRecord(_Record):
    part: str
    placeholder: str


class ScaffoldElementRecord(_Element):
    kind: Literal["scaffold"]
    text: str
    gaps: list[ScaffoldGapRecord]
    target: Literal["chat", "canvas"]


class PredictionElementRecord(_Element):
    kind: Literal["prediction"]
    prompt: str
    ask_confidence: bool


class FreeAnswerElementRecord(_Element):
    kind: Literal["free_answer"]
    prompt: str
    voice: bool


class PairElementRecord(_Element):
    kind: Literal["pair"]
    first_element_id: str
    second_element_id: str


class CardSeriesElementRecord(_Element):
    kind: Literal["card_series"]
    card_ids: list[str]


class TimedQuizElementRecord(_Element):
    kind: Literal["timed_quiz"]
    task_element_ids: list[str]
    time_limit_s: Annotated[int, Field(ge=1)]


class PlotSpecRecord(_Record):
    function: MathValue
    variable: str
    x_min: float
    x_max: float


class VisualizationElementRecord(_Element):
    kind: Literal["visualization"]
    request_id: UUID | None
    plot: PlotSpecRecord | None
    is_task: bool


class MaterialLinkElementRecord(_Element):
    kind: Literal["material_link"]
    chunk_id: UUID
    page: int | None


class ReferenceItemRecord(_Record):
    title: str
    text: str
    formula: MathValue | None


class ReferenceCardElementRecord(_Element):
    kind: Literal["reference_card"]
    items: list[ReferenceItemRecord]


class CodeTaskElementRecord(_Element):
    """Т-§6.10: a generated code task shown as it is (the editor opens on it)."""

    kind: Literal["code_task"]
    task_id: UUID


class CodeSandboxElementRecord(_Element):
    """The code sandbox (teacher@5): a code task written in the turn (`session.elements`). The
    hidden tests are never here: they live in the task document only."""

    kind: Literal["code_sandbox"]
    mode: Literal["inline_patch", "full_problem"]
    language: Literal["python", "sql"]
    statement: str
    starter_code: str
    entry_point: str | None
    sql_preset: str | None
    sample_args_json: str | None
    sample_expected_json: str
    compare: Literal["exact", "unordered", "float_approx"]
    epsilon: float | None


class SkillProposalElementRecord(_Element):
    kind: Literal["skill_proposal"]
    name: str
    text: str
    artifact_type: Literal["notes", "cards", "cheatsheet"] | None


type ElementRecord = Annotated[
    ChoiceElementRecord
    | WhyChoiceElementRecord
    | ShortAnswerElementRecord
    | TaskElementRecord
    | ScaffoldElementRecord
    | PredictionElementRecord
    | FreeAnswerElementRecord
    | PairElementRecord
    | CardSeriesElementRecord
    | TimedQuizElementRecord
    | VisualizationElementRecord
    | MaterialLinkElementRecord
    | ReferenceCardElementRecord
    | CodeTaskElementRecord
    | CodeSandboxElementRecord
    | SkillProposalElementRecord,
    Field(discriminator="kind"),
]


class ExposureRecord(_Record):
    """Т-§7.8 п.6: what the turn showed for a skill."""

    skill_id: UUID
    kind: ExposureKind
    approach: Approach


class BranchRecord(_Record):
    description: str
    type: BranchType


class LandmarkRefRecord(_Record):
    """A landmark of the session plan: a skill or a topic by id (labels are turn-local, so the
    journal keeps ids only)."""

    kind: Literal["skill", "topic"]
    id: UUID


class PlanStepRecord(_Record):
    """A step of the session plan. `id` is the teacher's slug of the step, stable within the
    session; events written before schema version 2 have the step text there (`upcast_plan_update`),
    so the id is not checked as a slug here — the guard of the turn checks it."""

    id: str
    text: str
    landmark: LandmarkRefRecord | None = None


class LandmarkAddRecord(_Record):
    """A landmark the teacher inserts before another one (`before_*` null: at the end)."""

    kind: Literal["skill", "topic"]
    id: UUID
    before_kind: Literal["skill", "topic"] | None = None
    before_id: UUID | None = None


class LandmarkEditsRecord(_Record):
    add: list[LandmarkAddRecord]
    remove: list[LandmarkRefRecord]


def upcast_plan_update(data: object) -> object:
    """Upcaster of `turn.committed` (Т-§2.1: old events are lifted when read, never rewritten).

    Schema version 1 wrote `{completed_steps, current_step: str, next_steps: [str], branches,
    cancel_request_ids}`. The same plan in the form of version 2: a step is its own text (the
    key of the old episode boundary), the history of done steps is not kept, there is no goal, no
    landmark and no edits of landmarks."""
    if not isinstance(data, dict) or "completed_steps" not in data:
        return data

    def step(text: str) -> dict[str, object]:
        return {"id": text, "text": text, "landmark": None}

    return {
        "goal": None,
        "current_step": step(data["current_step"]),
        "next_steps": [step(t) for t in data["next_steps"]],
        "landmark_edits": None,
        "branches": data["branches"],
        "cancel_request_ids": data["cancel_request_ids"],
    }


class PlanUpdateRecord(_Record):
    """Т-§12.5 with ids; `cancel_request_ids` — agent requests no longer needed (Т-§13.7).
    `None` is «no change» (the turn did not touch it): `goal`, `current_step`, `next_steps`,
    `landmark_edits`. The record accepts the form of schema version 1 too (`upcast_plan_update`)."""

    goal: str | None
    current_step: PlanStepRecord | None
    next_steps: list[PlanStepRecord] | None
    landmark_edits: LandmarkEditsRecord | None
    branches: list[BranchRecord]
    cancel_request_ids: list[UUID]

    @model_validator(mode="before")
    @classmethod
    def _upcast(cls, data: object) -> object:
        return upcast_plan_update(data)


class AgentParamsRecord(_Record):
    """Parameters of an agent request; fields that do not apply to the kind are null."""

    skill_id: UUID | None = None
    level: Level | None = None
    topic_id: UUID | None = None
    task_id: UUID | None = None
    misconception_id: UUID | None = None
    description: str | None = None
    # D-A-196 (#191, WP23.2, Т-§17.7): the artifact a request of kind `artifact` builds and the
    # hub of a cheat sheet — data, not words in `description` (law 2).
    hub_id: UUID | None = None
    artifact: Literal["notes", "cards", "cheatsheet"] | None = None
    # A demo prepared ahead for a step of the session plan (`viz.plan`): the slug of the step it
    # belongs to and its name for the learner. Null for every other request.
    step_id: str | None = None
    title: str | None = None
    # Т-§6.10: a `task_generation` request for a programming task in this language (the
    # answer type `code`); null — the answer type follows the level as before.
    language: Literal["python", "sql"] | None = None


class AgentRequestRecord(_Record):
    request_id: UUID
    kind: AgentKind
    params: AgentParamsRecord
    needed_in_session: bool


class DeadlineRecord(_Record):
    """A deadline heard in the conversation: an existing hub or a new one (WP13.3)."""

    hub_id: UUID | None
    new_hub_name: str | None
    deadline_at_ms: int
    coverage_topic_ids: list[UUID]


class TurnMarkupRecord(_Record):
    """`p3_markup` with ids (Т-§12.2, WP13.3)."""

    tasks: list[TurnTaskRecord]
    elements: list[ElementRecord]
    exposures: list[ExposureRecord]
    targets_misconception_id: UUID | None
    plan_update: PlanUpdateRecord
    agent_requests: list[AgentRequestRecord]
    corrections_acknowledged: list[int]
    decision_reason: str
    deadline: DeadlineRecord | None


# --- observations (Т-§7.6, Т-§9.3, Т-§9.4; B: ExtractedElement) --------------------------


class RootCauseRecord(_Record):
    """A weak prerequisite (Т-§9.4): the category is mapped to a number by code."""

    skill_id: UUID
    confidence: Literal["low", "medium", "high"]


class ExtractedElementRecord(_Record):
    """One element of `extractor@3` with ids. `events` — seq of the source events; `at_ms` —
    time of the learner action, copied by code from those events, not by the model (Т-§2.1)."""

    kind: Literal["evidence", "misconception_hypothesis"]
    skill_id: UUID | None
    new_skill: NewSkill | None
    skill_unclear: bool
    level: Level | None
    outcome: Literal["correct", "incorrect", "partial"] | None
    help: Literal["none", "hint", "shown_solution"]
    hint_step: Annotated[int, Field(ge=1, le=3)] | None
    gap_type: GapType
    misconception_id: UUID | None
    new_misconception: str | None
    root_cause: RootCauseRecord | None
    quote: str
    events: list[int]
    at_ms: int


class RejectedElementRecord(_Record):
    """An element the code rejected before it became evidence (Т-§7.6): its index in the
    extractor output, why and its quote (provenance, law 6; Q-A-2 п.3)."""

    index: int = Field(ge=0)
    reason: str
    quote: str


class DiagnosisRecord(_Record):
    """Т-§15.4: a step diagnosis of the canvas — gap type, misconception, root cause."""

    kind: Literal["error", "question"]
    gap_type: GapType
    misconception_id: UUID | None
    misconception_hypothesis: str | None
    root_cause_skill_id: UUID | None


# --- analyst (Т-§13.5; B: AnalystOutput) -------------------------------------------------


class FrontierItemRecord(_Record):
    skill_id: UUID
    level: Level
    state: str


class GapHypothesisRecord(_Record):
    skill_id: UUID
    gap_type: GapType
    reason: str


class UncertaintyRecord(_Record):
    question: str
    resolving_task: str


class SuggestedMoveRecord(_Record):
    move: str
    reason: str


class PreparationRecord(_Record):
    kind: Literal["viz", "illustration", "task_generation", "solution_space", "artifact"]
    skill_id: UUID | None
    level: Level | None
    misconception_id: UUID | None
    description: str


class DigestStructuredRecord(_Record):
    frontier: list[FrontierItemRecord]
    gap_hypotheses: list[GapHypothesisRecord]
    uncertainty: UncertaintyRecord | None
    moves: list[SuggestedMoveRecord]
    forecast: list[PreparationRecord]
