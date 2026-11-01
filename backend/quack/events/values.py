"""Value models shared by event payloads and stream B (ТЗ Ф4 WP2.1, seam S3; Q-A-1 variant (a)).

The catalog lives in the core (§4.1), so the payload models that stream B also uses in
`quack.practice` are defined here and re-exported by B. Shapes are those of B's models
(PR #72: `quack.practice.answer_spec`, `quack.practice.check`, `quack.practice.markup`,
`quack.practice.role_outputs`); field-level decisions are B's (D-B-107, D-B-112, D-B-113).
`CheckFailed` becomes a Pydantic model here (it is an event payload), B's dataclass of the same
fields is replaced by it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from quack.events.registry import EventPayload


class _Value(EventPayload):
    """Same config as payloads (unknown fields rejected, immutable); `Verdict` and
    `CheckFailed` are registered payloads themselves."""


# --- answers (Т-§5.2, Т-§6, Т-§22.7; B: quack.practice.answer_spec) ---------------------

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
# Т-§6.6 «разложено на множители, упрощено».
type FormTarget = Literal["factored", "simplified"]
# Т-§6.6 «вещественные, положительные и т. п.»: SymPy assumption names.
type Assumption = Literal["real", "positive", "nonnegative", "integer", "nonzero"]
# Т-§12.3, П-6.4: self-reported confidence 1–4.
type Confidence = Annotated[int, Field(ge=1, le=4)]
type Level = Annotated[int, Field(ge=1, le=6)]


class MathValue(_Value):
    """Т-§22.7: `latex` for display, `sympy` — canonical SymPy string for checking."""

    latex: str
    sympy: str


class Variable(_Value):
    name: str
    assumptions: list[Assumption]


class AnswerOption(_Value):
    """One option of a `choice` answer; `option_id` is local to the task (`A`, `B`, …)."""

    option_id: str
    text: str
    correct: bool


class AnswerSpec(_Value):
    """Т-§5.2 `answer` (WP11.1)."""

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


class LearnerAnswer(_Value):
    """`answer.submitted.answer` (§6.3): raw `value` (option id for `choice`), `latex` /
    `sympy` from the formula editor or the recognizer (Т-§6.5, Т-§22.7)."""

    kind: AnswerType
    value: str
    latex: str | None = None
    sympy: str | None = None


# --- verdicts (Т-§6.2, WP11.4; B: quack.practice.check) ---------------------------------

type Outcome = Literal["correct", "incorrect", "partial", "unclear"]
type Method = Literal["exact", "numeric", "symbolic", "units", "model"]
type Verification = Literal["code", "model"]
# Т-§6.2 ориентир: cheap deterministic checks of a wrong answer.
type ErrorHint = Literal["sign", "coefficient", "dimension"]
type CheckFailedReason = Literal["unparsed", "too_few_points", "timeout", "compute_unavailable"]


class SecondOpinion(_Value):
    """Т-§7.7: whether a second opinion was asked and whether it agreed (null — not asked)."""

    requested: bool
    agreed: bool | None = None


class RubricItemResult(_Value):
    """One rubric item of a model verdict (Т-§6.8)."""

    item: str
    status: Literal["found", "missing", "wrong"]
    quote: str | None = None


class Verdict(_Value):
    """`verdict.recorded` payload (Т-§6.2, WP11.4). Key `verdict:{answer_seq}:{part}`.

    `help_step` / `solution_shown` — help computed by code (WP11.2). `misconception` — in the
    event a misconception id or a hypothesis in words, never a short label (Т-§4.5).
    """

    answer_seq: int
    part: str
    outcome: Outcome
    method: Method
    verification: Verification
    numeric_only: bool = False
    error_hint: ErrorHint | None = None
    second_opinion: SecondOpinion | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    help_step: int = Field(default=0, ge=0, le=3)
    solution_shown: bool = False
    rubric: list[RubricItemResult] | None = None
    misconception: str | None = None
    needs_reading_confirmation: bool = False


class CheckFailed(_Value):
    """`check.failed {answer_seq, part, reason}` (Т-§22.7): goes to `check.model`."""

    answer_seq: int
    part: str
    reason: CheckFailedReason


# --- markup and observations (B: quack.practice.markup, quack.practice.role_outputs) -----

type TaskForm = Literal["multistep", "explain_why", "what_breaks"]
type GapType = Literal[
    "no_idea", "when_to_apply", "confusion", "execution", "slip", "misconception", "none"
]


class NewSkill(_Value):
    """Description of a new skill (Т-§4.4): name and the topic it belongs to in words."""

    name: str
    topic_hint: str
