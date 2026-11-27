"""Output of a teacher turn `teacher@3` (Т-§12.2; ТЗ Ф4 WP13.3, seam S11; `teacher@2` before the
plan graph).

Three nested objects; the model writes JSON keys in alphabetical order (Т-§11.8), so the
names `p1_head` < `p2_body` < `p3_markup` give the head first. Field names inside the
objects are those of Т-§12.2 / ТЗ Ф4 WP13.3 (`targetSkills`, `verdict_stance`,
`branchType`, `planUpdate`, …): they are the model output contract. The model writes
labels, code translates them to ids (Т-§4.5); an unknown label is returned to the model.

`intent` and `approach` lists are provisional until the owner's doctrine (WP13.3, D-B-104,
class C): `intent` follows the turn choices of П-4.4, `approach` the examples of Т-§9.5 and
the techniques of П-4.2.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.practice.markup import TaskMarkup
from quack.session.elements import Element

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
type AgentRequestKind = Literal[
    "viz", "illustration", "task_generation", "solution_space", "artifact", "known_check"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class TurnHead(_Strict):
    """`p1_head` (Т-§12.2): checked by code before any body character is shown. `topic_label`
    (`T…`) — the topic the learner asks to split, only with `intent = split_topic`
    (WP8.2, D-B-213)."""

    intent: TurnIntent
    target_skills: list[str] = Field(alias="targetSkills")
    verdict_stance: VerdictStance | None
    branch_type: BranchType | None = Field(alias="branchType")
    approach: Approach
    topic_label: str | None = Field(alias="topicLabel")


class BodyBlock(_Strict):
    """A block of `p2_body.blocks`: text (`text`) or a reference to an element
    (`element_id`); exactly one is set (D-B-110)."""

    block_id: str
    text: str | None
    element_id: str | None


class TurnBody(_Strict):
    blocks: list[BodyBlock]


class TurnTask(TaskMarkup):
    """`p3_markup.tasks`: markup of a task shown as an element (`element_id`)."""

    element_id: str


class Exposure(_Strict):
    """`p3_markup.exposures` (Т-§7.8 п.6): what the turn showed for a skill (label)."""

    skill: str
    kind: ExposureKind
    approach: Approach


class Branch(_Strict):
    """An off-plan branch with its type (Т-§12.5, Т-§12.6)."""

    description: str
    type: BranchType


class Step(_Strict):
    """A step of the plan. `id` — the slug of the step (`a-z0-9_-`), the same in every turn while
    it is the same step, so a reworded step is not a new one; `text` — what the learner sees;
    `landmark` — a label `S…` (skill) or `T…` (topic) of the turn's table, or null."""

    id: str
    text: str
    landmark: str | None


class LandmarkAdd(_Strict):
    """A skill or topic (label `S…`/`T…`) the teacher adds to the landmarks, before the landmark
    `before` (the same kind of labels), or at the end when `before` is null."""

    label: str
    before: str | None


class LandmarkEdits(_Strict):
    """Rare corrections of the landmarks code built from memory; `remove` — labels `S…`/`T…`."""

    add: list[LandmarkAdd]
    remove: list[str]


class PlanUpdate(_Strict):
    """`planUpdate` (Т-§12.5, WP13.6; the plan graph): everything but `branches` and
    `cancel_requests` is `null` for «no change» — `goal` is written once, the steps only when they
    change, `landmark_edits` rarely. The position in the plan is computed by code from the steps.
    `next_steps` is at most `Settings.plan_next_steps_max` long: the limit is a parameter, so the
    guard settles it (`guards.drop_unknown_markup_labels`: a slug is made of a step `id`, the list
    is cut) and logs it, instead of the schema failing the whole answer; the turn is not returned
    to the model, its text is already shown. `cancel_requests` — labels `R…` of
    agent requests no longer needed (Т-§13.7, D-B-108)."""

    goal: str | None
    current_step: Step | None
    next_steps: list[Step] | None
    landmark_edits: LandmarkEdits | None
    branches: list[Branch]
    cancel_requests: list[str]


class AgentRequestParams(_Strict):
    """Parameters of an agent request; fields that do not apply to the kind are null
    (strict schema has no free dicts, Т-§11.1; D-B-110). Labels: `skill` `S…`, `topic`
    `T…`, `task` `K…`, `misconception` `M…`."""

    skill: str | None
    level: int | None = Field(ge=1, le=6)
    topic: str | None
    task: str | None
    misconception: str | None
    description: str | None
    # `teacher@4`, Т-§6.10: a `task_generation` request for a programming task in this language;
    # null — the answer type follows the level as before.
    language: Literal["python", "sql"] | None = None


class AgentRequest(_Strict):
    """`agentRequests` (Т-§10.4, Т-§13.2, WP16.6)."""

    kind: AgentRequestKind
    params: AgentRequestParams
    needed_in_session: bool


type ControlType = Literal["tasks", "colloquium_proofs", "oral_theory"]


class DeadlineFromTalk(_Strict):
    """A deadline heard in the conversation (WP13.3): an existing hub (`hub_label` `H…`) or a
    new one (`new_hub_name`); `deadline_at` — ISO 8601 date-time; `coverage` — topic labels
    the deadline covers (D-B-110). `quote` — the learner's own words that name the control
    and its date, copied exactly: code writes the deadline only if they are in a learner
    message of the session (law 6, D-B-149); `control_type` — the kind of control when the
    learner named it, for the required levels of a new hub (Т-§4.8, Т-§8.5)."""

    hub_label: str | None
    new_hub_name: str | None
    deadline_at: str
    coverage: list[str]
    quote: str
    control_type: ControlType | None


class TurnMarkup(_Strict):
    """`p3_markup` (Т-§12.2, WP13.3)."""

    tasks: list[TurnTask]
    elements: list[Element]
    exposures: list[Exposure]
    targets_misconception: str | None = Field(alias="targetsMisconception")
    plan_update: PlanUpdate = Field(alias="planUpdate")
    agent_requests: list[AgentRequest] = Field(alias="agentRequests")
    corrections_acknowledged: list[int] = Field(alias="correctionsAcknowledged")
    decision_reason: str = Field(alias="decisionReason")
    deadline: DeadlineFromTalk | None


class TeacherOutputV2(_Strict):
    """`teacher@3` and `teacher@4` (code tasks: `params.language`, the `code_task` element);
    `teacher_fallback` uses the same prompt and schema (ТЗ Ф4 §6.6)."""

    p1_head: TurnHead
    p2_body: TurnBody
    p3_markup: TurnMarkup


class PlanUpdateLegacyV2(_Strict):
    """`planUpdate` of `teacher@2` (before the plan graph): kept for offline replay."""

    completed_steps: list[str]
    current_step: str
    next_steps: list[str] = Field(max_length=2)
    branches: list[Branch]
    cancel_requests: list[str]


class TurnMarkupLegacyV2(_Strict):
    """`p3_markup` of `teacher@2` (before the plan graph)."""

    tasks: list[TurnTask]
    elements: list[Element]
    exposures: list[Exposure]
    targets_misconception: str | None = Field(alias="targetsMisconception")
    plan_update: PlanUpdateLegacyV2 = Field(alias="planUpdate")
    agent_requests: list[AgentRequest] = Field(alias="agentRequests")
    corrections_acknowledged: list[int] = Field(alias="correctionsAcknowledged")
    decision_reason: str = Field(alias="decisionReason")
    deadline: DeadlineFromTalk | None


def _legacy_step_id(text: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-") or "step"


class TeacherOutputLegacyV2(_Strict):
    """Output schema of `teacher@2`, lifted to `TeacherOutputV2` after parsing."""

    p1_head: TurnHead
    p2_body: TurnBody
    p3_markup: TurnMarkupLegacyV2

    def to_current(self) -> TeacherOutputV2:
        old = self.p3_markup.plan_update
        plan = PlanUpdate(
            goal=None,
            current_step=(
                Step(id=_legacy_step_id(old.current_step), text=old.current_step, landmark=None)
                if old.current_step.strip()
                else None
            ),
            next_steps=[
                Step(id=_legacy_step_id(s), text=s, landmark=None)
                for s in old.next_steps
                if s.strip()
            ],
            landmark_edits=None,
            branches=old.branches,
            cancel_requests=old.cancel_requests,
        )
        markup = TurnMarkup(
            tasks=self.p3_markup.tasks,
            elements=self.p3_markup.elements,
            exposures=self.p3_markup.exposures,
            targetsMisconception=self.p3_markup.targets_misconception,
            planUpdate=plan,
            agentRequests=self.p3_markup.agent_requests,
            correctionsAcknowledged=self.p3_markup.corrections_acknowledged,
            decisionReason=self.p3_markup.decision_reason,
            deadline=self.p3_markup.deadline,
        )
        return TeacherOutputV2(p1_head=self.p1_head, p2_body=self.p2_body, p3_markup=markup)


def parse_legacy_teacher_output(raw: str) -> TeacherOutputV2:
    return TeacherOutputLegacyV2.model_validate_json(raw).to_current()


def teacher_schema_for_prompt(
    prompt_name: str, version: int
) -> tuple[type[BaseModel], Callable[[str], TeacherOutputV2]]:
    """Select the strict JSON schema and parser matching `prompt_name` and `version`."""
    if prompt_name == "teacher" and version == 2:
        return TeacherOutputLegacyV2, parse_legacy_teacher_output
    return TeacherOutputV2, TeacherOutputV2.model_validate_json
