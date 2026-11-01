"""Output of a teacher turn `teacher@2` (Т-§12.2; ТЗ Ф4 WP13.3, seam S11).

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
    """`p1_head` (Т-§12.2): checked by code before any body character is shown."""

    intent: TurnIntent
    target_skills: list[str] = Field(alias="targetSkills")
    verdict_stance: VerdictStance | None
    branch_type: BranchType | None = Field(alias="branchType")
    approach: Approach


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


class PlanUpdate(_Strict):
    """`planUpdate` (Т-§12.5, WP13.6): done steps, the current one, one or two hypotheses of
    the next, branches; `cancel_requests` — labels `R…` of agent requests no longer needed
    (Т-§13.7, D-B-108)."""

    completed_steps: list[str]
    current_step: str
    next_steps: list[str] = Field(max_length=2)
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


class AgentRequest(_Strict):
    """`agentRequests` (Т-§10.4, Т-§13.2, WP16.6)."""

    kind: AgentRequestKind
    params: AgentRequestParams
    needed_in_session: bool


class DeadlineFromTalk(_Strict):
    """A deadline heard in the conversation (WP13.3): an existing hub (`hub_label` `H…`) or a
    new one (`new_hub_name`); `deadline_at` — ISO 8601 date-time; `coverage` — topic labels
    the deadline covers (D-B-110)."""

    hub_label: str | None
    new_hub_name: str | None
    deadline_at: str
    coverage: list[str]


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
    """`teacher@2`; `teacher_fallback` uses the same prompt and schema (ТЗ Ф4 §6.6)."""

    p1_head: TurnHead
    p2_body: TurnBody
    p3_markup: TurnMarkup
