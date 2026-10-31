"""`core.task_bank.reference_solution` — one shared model (WP6.4 п.1, seam S10).

Keys by task kind follow the table of WP6.4; `hints` (hint ladder of practice) and `latex`
(display) are a compatible extension of that table. Pending owner confirmation: D-B-001.
A record of a wrong format is rejected when written to the bank, not when an answer is
checked.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from quack.compute_client.client import MathMode
from quack.events.payloads import MisconceptionEvalHit, SkillEvalScore, Unit

# --- Prompt output schemas of practice (moved from quack/artifacts/schemas.py, D-C-013) ---


class TaskGenOutput(BaseModel):
    """task_gen@1. `reference_solution` becomes `ReferenceSolution` before G1 (D-B-001)."""

    family_id: str
    difficulty: Unit
    prompt_text: str
    reference_solution: dict[str, Any]
    target_misconceptions: list[str] = []


class EvaluatorOutput(BaseModel):
    """evaluator@1."""

    is_correct: bool
    score: Unit
    feedback: str
    skill_scores: list[SkillEvalScore]
    triggered_misconceptions: list[MisconceptionEvalHit] = []


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
