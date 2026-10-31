"""`core.task_bank.reference_solution` — one shared model (WP6.4 п.1, seam S10).

Keys by task kind follow the table of WP6.4; `hints` (hint ladder of practice) and `latex`
(display) are a compatible extension of that table. Decided: D-B-001 (1).
A record of a wrong format is rejected when written to the bank, not when an answer is
checked.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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

_Text = Annotated[str, Field(min_length=1)]


class _Reference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hints: list[str] | None = None


class MathReference(_Reference):
    expression: _Text
    mode: MathMode = "expression_equiv"
    variables: list[str] = ["x"]
    tolerance: float = 1e-6
    latex: str | None = None


class ChoiceReference(_Reference):
    option: _Text
    answer: _Text
    options: Annotated[list[_Text], Field(min_length=2)]

    @model_validator(mode="after")
    def _option_is_listed(self) -> ChoiceReference:
        if self.option not in self.options:
            raise ValueError("the correct option is not among the options")
        return self


class OpenReference(_Reference):
    key_points: Annotated[list[_Text], Field(min_length=1)]
    sample_answer: _Text


type ReferenceSolution = MathReference | ChoiceReference | OpenReference

# `code` tasks are checked like `open` ones in Phase 3: no runner, the evaluator reads the
# key points (D-B-025).
REFERENCE_MODELS: dict[str, type[MathReference | ChoiceReference | OpenReference]] = {
    "math": MathReference,
    "choice": ChoiceReference,
    "open": OpenReference,
    "code": OpenReference,
}


def parse_reference_solution(kind: str, data: dict[str, object]) -> ReferenceSolution:
    """The model of `kind` validated from the stored dict; `ValueError` on a wrong format."""
    model = REFERENCE_MODELS.get(kind)
    if model is None:
        raise ValueError(f"unknown task kind {kind!r}")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise ValueError(f"reference_solution of a {kind} task: {exc}") from exc


def reference_solution_payload(reference: ReferenceSolution) -> dict[str, object]:
    """The dict stored in `core.task_bank.reference_solution` and in `task.generated`."""
    return reference.model_dump(mode="json", exclude_none=True)
