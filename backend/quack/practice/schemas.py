"""`core.task_bank.reference_solution` — one shared model (WP6.4 п.1, seam S10).

Keys by task kind follow the table of WP6.4; `hints` (hint ladder of practice) and `latex`
(display) are a compatible extension of that table. Pending owner confirmation: D-B-001.
A record of a wrong format is rejected when written to the bank, not when an answer is
checked.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.compute_client.client import MathMode
from quack.events.payloads import Unit

# --- Prompt output schemas of practice (moved from quack/artifacts/schemas.py, D-C-013) ---


class TaskOption(BaseModel):
    """`catches`: null only for the correct option; a misconception label (`M1`...),
    "neighbor_idea" or "slip" for a wrong one."""

    label: str
    text: str
    correct: bool
    catches: str | None


type AnswerType = Literal[
    "choice", "number", "expression", "set", "vector", "quantity", "proof", "free_text"
]


class TaskAnswer(BaseModel):
    """`value`: the option label for `choice`; SymPy syntax for math types."""

    type: AnswerType
    value: str
    units: str | None


class TaskLevel(BaseModel):
    skill: str
    level: int = Field(ge=1, le=6)


class PairTask(BaseModel):
    statement: str
    answer: str
    differs_by: str


class TaskGenOutput(BaseModel):
    """task_gen@1 (D-B-010): the contract of the prompt plus the hint ladder `hints`; code
    builds the `core.task_bank` row from it (`quack.practice.generation`)."""

    statement: str
    options: list[TaskOption]
    answer: TaskAnswer
    solution: str
    hints: list[str]
    tests: list[TaskLevel]
    method_named: bool
    variant_of: str | None
    form: Literal["multistep", "explain_why", "what_breaks"] | None
    pair_task: PairTask | None


class RubricItemCheck(BaseModel):
    """One rubric item of `evaluator@1`: `item` is its label (`R1`...)."""

    item: str
    status: Literal["found", "missing", "wrong"]
    quote: str | None


class EvaluatorMisconception(BaseModel):
    """`label`: a known label (`M1`...) or `new: <the wrong belief in plain words>`."""

    label: str
    quote: str


class EvaluatorOutput(BaseModel):
    """evaluator@1 (D-B-009): the contract of the prompt; code maps `verdict` to the fields
    of `task.evaluated` (`quack.practice.evaluation`)."""

    items: list[RubricItemCheck]
    verdict: Literal["correct", "partial", "incorrect", "unclear"]
    confidence: Unit
    misconception: EvaluatorMisconception | None
    note: str


# --- core.task_bank.reference_solution (WP6.4) ---

type TaskKind = Literal["math", "choice", "open"]


class _Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hints: list[str] | None = None


class MathReference(_Reference):
    expression: str
    mode: MathMode = "expression_equiv"
    variables: list[str] = ["x"]
    tolerance: float = 1e-6
    latex: str | None = None


class ChoiceReference(_Reference):
    option: str
    answer: str
    options: list[str]


class OpenReference(_Reference):
    key_points: list[str]
    sample_answer: str


type ReferenceSolution = MathReference | ChoiceReference | OpenReference


def parse_reference_solution(kind: str, data: dict[str, object]) -> ReferenceSolution:
    """The model of `kind` validated from the stored dict; `ValueError` on a wrong format."""
    raise NotImplementedError


def reference_solution_payload(reference: ReferenceSolution) -> dict[str, object]:
    """The dict stored in `core.task_bank.reference_solution` and in `task.generated`."""
    raise NotImplementedError
