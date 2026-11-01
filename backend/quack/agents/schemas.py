"""Output schemas of the agent roles of stream B (ТЗ Ф4 §6.6, seam S8).

Closed enums, labels instead of ids (Т-§4.5), no strength numbers (law 4, `T-W1`): the
extractor's root-cause confidence is a category that code maps by
`root_cause_confidence_map` (WP15 п.3). Shapes beyond the fields named in Т-§ — D-B-113.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.practice.markup import NewSkill
from quack.practice.role_outputs import GapType

type Level = Annotated[int, Field(ge=1, le=6)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- extractor@3 (Т-§7.6; ТЗ Ф4 WP15 п.3) ---


class RootCause(_Strict):
    """A weak prerequisite (Т-§9.4): skill label and a confidence category."""

    skill: str
    confidence: Literal["low", "medium", "high"]


class ExtractedElement(_Strict):
    """One element of `extractor@3`. Skill: a label (`skill`), a new one (`new_skill`, only
    with a quote and if no candidate fits, Т-§4.4) or unclear (`skill_unclear`, no
    evidence). Misconception: a label (`misconception`) or a new statement
    (`misconception_statement`). `events` — seq numbers of the source events."""

    kind: Literal["evidence", "misconception_hypothesis"]
    skill: str | None
    new_skill: NewSkill | None
    skill_unclear: bool
    level: Level | None
    outcome: Literal["correct", "incorrect", "partial"] | None
    help: Literal["none", "hint", "shown_solution"]
    hint_step: Annotated[int, Field(ge=1, le=3)] | None
    gap_type: GapType
    misconception: str | None
    misconception_statement: str | None
    root_cause: RootCause | None
    quote: str
    events: list[int]


class ExtractorOutputV3(_Strict):
    elements: list[ExtractedElement]


# --- analyst@1 / analyst_deep (Т-§13.5; ТЗ Ф4 WP16.1) ---


class FrontierItem(_Strict):
    """The frontier on a target skill: the first level not yet proven, in words."""

    skill: str
    level: Level
    state: str


class GapHypothesis(_Strict):
    skill: str
    gap_type: GapType
    reason: str


class Uncertainty(_Strict):
    """An uncertainty that changes the next step and the task that would resolve it."""

    question: str
    resolving_task: str


class SuggestedMove(_Strict):
    move: str
    reason: str


class PreparationRequest(_Strict):
    """The forecast: what will be needed soon → `agent.requested` (Т-§13.2)."""

    kind: Literal["viz", "illustration", "task_generation", "solution_space", "artifact"]
    skill: str | None
    level: Level | None
    misconception: str | None
    description: str


class AnalystOutput(_Strict):
    """`digest.written` source (Т-§13.5): `text` — the digest for the teacher; `manner` —
    the teacher's manner for this learner, written anew (Т-§9.5, WP5.3)."""

    frontier: list[FrontierItem]
    gap_hypotheses: list[GapHypothesis]
    uncertainty: Uncertainty | None
    plan_divergence: bool
    moves: list[SuggestedMove] = Field(min_length=2, max_length=3)
    forecast: list[PreparationRequest]
    manner: str
    text: str


# --- builder@1: reconciliation (Т-§4.3) and hygiene pairs (Т-§4.10) ---


class ReconciledItem(_Strict):
    """One subject found in the text (`name`, `kind`): `link` to a candidate label, `create`,
    `suggest_merge` with a candidate label; `same_idea` — a skill of another domain (label)
    with a confidence category; `topic_hint` — the topic a new skill belongs to."""

    name: str
    kind: Literal["topic", "skill"]
    decision: Literal["link", "create", "suggest_merge"]
    target: str | None
    topic_hint: str | None
    same_idea: str | None
    same_idea_confidence: Literal["low", "medium", "high"] | None


class BuilderOutput(_Strict):
    items: list[ReconciledItem]


class DuplicateVerdict(_Strict):
    """A candidate pair `first`/`second` (labels) of the hygiene: duplicates or not."""

    first: str
    second: str
    duplicate: bool


class HygieneOutput(_Strict):
    pairs: list[DuplicateVerdict]


# --- misconception_describer@1 (Т-§9.3) ---


class MisconceptionDescription(_Strict):
    statement: str
    triggers: list[str]


# --- today_planner@1 (Т-§14.4, WP16.8) ---


class TodayItemPlan(_Strict):
    """For one recommendation: bank tasks (labels `K…`) or a request to generate."""

    recommendation: str
    tasks: list[str]
    generate: bool
    plan_steps: list[str]


class TodayPlannerOutput(_Strict):
    items: list[TodayItemPlan]


# --- domain_classifier@1 (Т-§4.9, WP16.4) ---


class DomainClassifierOutput(_Strict):
    """`existing` with a domain label, `new` with a name, or `unclear`; `confidence` is
    compared with `domain_autolink_confidence` (Т-§4.9 ориентир)."""

    decision: Literal["existing", "new", "unclear"]
    domain: str | None
    new_name: str | None
    confidence: float = Field(ge=0.0, le=1.0)


# --- viz_builder@1 (Т-§13.6, WP16.5) ---


class VizBuilderOutput(_Strict):
    """One-page HTML or SVG; `parameter_values` — values to render it at (compute)."""

    format: Literal["html", "svg"]
    content: str
    parameter_values: list[str]
    answer_action: str | None


# --- release_judge@1 (Т-§18.4, WP16.7) ---


class ReleaseJudgeOutput(_Strict):
    winner: Literal["a", "b", "tie"]
    reason: str
