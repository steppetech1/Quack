"""Answer specification and the learner's answer (Т-§5.2, Т-§6, Т-§22.7; ТЗ Ф4 WP11.1, seam S7).

One model shared by the task generator, the teacher (`p3_markup.tasks`), cards and the canvas.
The model writes it, so it is strict-schema friendly: no free dicts, optional fields are
nullable (Т-§11.1). Every expression carries two fields, `latex` for display and `sympy` for
checking (Т-§22.7). Field-level decisions — D-B-107.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# Т-§5.2 answer types plus the short exact answer of Т-§6.4 (`short_text`, D-B-107).
type AnswerType = Literal[
    "choice",
    "number",
    "expression",
    "set",
    "vector",
    "quantity",
    "proof",
    "free_text",
    "drawing",
    "short_text",
]

# Т-§6.6: comparison modes of math answers.
type CompareMode = Literal["equal", "up_to_constant", "solution_set", "interval", "form"]

# Т-§6.6 «разложено на множители, упрощено»: the form required in `form` mode.
type FormTarget = Literal["factored", "simplified"]

# Т-§6.6 «вещественные, положительные и т. п.»: SymPy assumption names.
type Assumption = Literal["real", "positive", "nonnegative", "integer", "nonzero"]

# Т-§6.1: which speed checks a type.
INSTANT_TYPES: frozenset[str] = frozenset({"choice", "number", "short_text", "set", "vector"})
FAST_TYPES: frozenset[str] = frozenset({"expression", "quantity"})
SLOW_TYPES: frozenset[str] = frozenset({"proof", "free_text", "drawing"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MathValue(_Strict):
    """Т-§22.7: `latex` for display (KaTeX), `sympy` — canonical SymPy string for checking."""

    latex: str
    sympy: str


class Variable(_Strict):
    """A variable of the expression and its assumptions (Т-§6.6)."""

    name: str
    assumptions: list[Assumption]


class AnswerOption(_Strict):
    """One option of a `choice` answer; `option_id` is local to the task (`A`, `B`, …)."""

    option_id: str
    text: str
    correct: bool


class AnswerSpec(_Strict):
    """Т-§5.2 `answer`: type, correct answer, tolerance, units, comparison mode (WP11.1).

    - `value` — the correct answer of `number`, `expression`, `quantity` (the magnitude);
    - `items` — elements of `set` / `vector`;
    - `options` — options of `choice` (exactly one `correct`, checked by `validate_markup`);
    - `accepted` — accepted answers of `short_text`;
    - `key_ideas` — rubric of `proof` / `free_text` / `drawing` when there is no solution
      space (Т-§6.8).
    """

    type: AnswerType
    value: MathValue | None = None
    items: list[MathValue] | None = None
    options: list[AnswerOption] | None = None
    accepted: list[str] | None = None
    tol_abs: Annotated[float, Field(ge=0)] | None = None
    tol_rel: Annotated[float, Field(ge=0)] | None = None
    sig_digits: Annotated[int, Field(ge=1)] | None = None
    unit: str | None = None
    mode: CompareMode | None = None
    form_target: FormTarget | None = None
    variables: list[Variable] | None = None
    key_ideas: list[str] | None = None


class LearnerAnswer(_Strict):
    """`answer.submitted.answer` (ТЗ Ф4 §6.3): `{kind, value, latex?, sympy?}`; `value` is the
    raw text (option id for `choice`), `latex` / `sympy` come from the formula editor or the
    recognizer (Т-§6.5, Т-§22.7)."""

    kind: AnswerType
    value: str
    latex: str | None = None
    sympy: str | None = None
