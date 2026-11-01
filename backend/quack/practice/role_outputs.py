"""Output schemas of the practice roles (ТЗ Ф4 §6.6, seam S8): model verdicts
(`verdict_free`, `verdict_proof`, `second_opinion`, Т-§6.8, Т-§7.7), task generation and
checking (`task_generator`, `task_checker`, `task_arbiter`, Т-§5.6) and the solution space
(`solution_space`, Т-§5.5). Labels instead of ids (Т-§4.5); no strength numbers (law 4) —
`confidence` of a verdict is only the trigger of a second opinion (Т-§7.7). Shapes —
D-B-113.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.practice.answer_spec import MathValue
from quack.practice.markup import TaskMarkup

# Т-§7.6, П-6.4: gap types; positive elements carry `none`. Shared by the extractor, the
# wrong branches of a solution space (Т-§5.5) and `gapType` (Т-§8.4).
type GapType = Literal[
    "no_idea", "when_to_apply", "confusion", "execution", "slip", "misconception", "none"
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


# --- verdict_free@1, verdict_proof@1, second_opinion@1 (Т-§6.8, Т-§7.7) ---


class RubricItemCheck(_Strict):
    """One rubric item (`R1`, … as given in the input): found, missing or wrong, with a quote
    from the answer (null only for `missing`)."""

    item: str
    status: Literal["found", "missing", "wrong"]
    quote: str | None


class ModelVerdictOutput(_Strict):
    """Т-§6.8: per rubric item, the overall outcome and the confidence. `misconception` — a
    known label `M…`; `misconception_hypothesis` — a new one in words; at most one is set."""

    items: list[RubricItemCheck]
    outcome: Literal["correct", "incorrect", "partial", "unclear"]
    confidence: float = Field(ge=0.0, le=1.0)
    misconception: str | None
    misconception_hypothesis: str | None


# --- solution_space@1 (Т-§5.5) ---


class KeyStep(_Strict):
    statement: str
    expression: MathValue | None


class WrongBranch(_Strict):
    """A typical wrong branch: its sign, gap type, misconception label or null."""

    sign: str
    gap_type: GapType
    misconception: str | None


class SolutionSpace(_Strict):
    """Т-§5.5: 3–8 key steps, 2–4 wrong branches, a ladder of 3 hints; the answer is the
    task's spec (not repeated). Stored in `content.tasks.solution_space` with a version."""

    key_steps: list[KeyStep] = Field(min_length=3, max_length=8)
    wrong_branches: list[WrongBranch] = Field(min_length=2, max_length=4)
    hints: list[str] = Field(min_length=3, max_length=3)


# --- task_generator@1 (Т-§5.4, Т-§5.6, Т-§5.7) ---


class TaskCondition(_Strict):
    """The condition of a task: text, and LaTeX of formulas if any (Т-§5.1)."""

    text: str
    latex: str | None


class GeneratedTaskDraft(_Strict):
    """One variant; variants of one call share a `familyId` set by code (Т-§5.4)."""

    condition: TaskCondition
    markup: TaskMarkup
    solution: str


class TaskGeneratorOutput(_Strict):
    tasks: list[GeneratedTaskDraft] = Field(min_length=1)


# --- task_checker@1, task_arbiter@1 (Т-§5.6) ---


class TaskCheckOutput(_Strict):
    """The checker solves the task independently (`answer` — in the type of the spec: option
    id, number or `latex`/`sympy`) and judges the truth of the markup: is the method named,
    is it a variant of the given task (null — no `variantOf`), does it fit each tested
    level (`levels_fit`, in the order of `tests`). Code compares `answer` with the key by
    `check()` (Т-§5.6)."""

    answer_text: str
    answer_math: MathValue | None
    method_named: bool = Field(alias="methodNamed")
    is_variant: bool | None
    levels_fit: list[bool]
    note: str
