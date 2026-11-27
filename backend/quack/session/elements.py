"""Interactive elements of a teacher turn (Т-§12.3; ТЗ Ф4 WP13.7, seam S11).

`Element` is a union discriminated by `kind`; it is written by the model in
`p3_markup.elements`, sent to the client in the SSE frame `element` and appears in OpenAPI.
Every element that checks understanding is a task: its spec and markup live in
`p3_markup.tasks` under the same `element_id` (Т-§12.3, WP13.7), the element carries only
what the interface shows. References use short labels (`K`, `C`, `P`, Т-§4.5, D-B-108).
Kind names and fields — D-B-109.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.practice.answer_spec import MathValue

# Elements whose answer is checked (`check()`): they need a task in `p3_markup.tasks`.
TASK_ELEMENT_KINDS: frozenset[str] = frozenset(
    {
        "choice",
        "why_choice",
        "short_answer",
        "task",
        "scaffold",
        "prediction",
        "free_answer",
        "code_sandbox",
    }
)


class _Element(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    element_id: str


class ChoiceElement(_Element):
    """Вопрос с выбором: options are in the task spec (`answer.options`)."""

    kind: Literal["choice"]
    prompt: str
    ask_confidence: bool


class WhyChoiceElement(_Element):
    """«Почему» как выбор: 3–4 reasonings and «other» (П-4.5); options in the task spec."""

    kind: Literal["why_choice"]
    prompt: str
    allow_other: bool
    ask_confidence: bool


class ShortAnswerElement(_Element):
    """Короткий ответ: the answer type and spec are in the task."""

    kind: Literal["short_answer"]
    prompt: str
    ask_confidence: bool


class TaskElement(_Element):
    """Задача, with the «solve on the canvas» button."""

    kind: Literal["task"]
    prompt: str
    canvas: bool
    ask_confidence: bool


class ScaffoldGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    part: str
    placeholder: str


class ScaffoldElement(_Element):
    """Каркас с пропусками: every gap is a part of the task spec (Т-§5.2); `target` —
    chat or a block in the teacher layer of the canvas (Т-§15.1)."""

    kind: Literal["scaffold"]
    text: str
    gaps: list[ScaffoldGap]
    target: Literal["chat", "canvas"]


class PredictionElement(_Element):
    """Предсказание: a question before a step of the explanation."""

    kind: Literal["prediction"]
    prompt: str
    ask_confidence: bool


class FreeAnswerElement(_Element):
    """Свободный ответ, «объясни своими словами»: the rubric is in the task spec
    (`answer.key_ideas`); the answer is text or voice."""

    kind: Literal["free_answer"]
    prompt: str
    voice: bool


class PairElement(_Element):
    """Пара на различение: two task elements whose markups point at each other
    (`pairTaskId`)."""

    kind: Literal["pair"]
    first_element_id: str
    second_element_id: str


class CardSeriesElement(_Element):
    """Серия карточек: card labels (`C…`, §17)."""

    kind: Literal["card_series"]
    cards: list[str]


class TimedQuizElement(_Element):
    """Пробник с таймером: task elements and the time."""

    kind: Literal["timed_quiz"]
    task_element_ids: list[str]
    time_limit_s: Annotated[int, Field(ge=1)]


class PlotSpec(BaseModel):
    """A simple plot of one function (Т-§12.3 «простой график»)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    function: MathValue
    variable: str
    x_min: float
    x_max: float


class VisualizationElement(_Element):
    """Визуализация, демо: a ready artifact (agent request label `R…`, Т-§13.6) or a simple
    plot; exactly one is set."""

    kind: Literal["visualization"]
    request: str | None
    plot: PlotSpec | None
    is_task: bool


class MaterialLinkElement(_Element):
    """Ссылка на материал: a chunk label (`P…`) and its page or slide."""

    kind: Literal["material_link"]
    chunk: str
    page: int | None


class ReferenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    text: str
    formula: MathValue | None


class ReferenceCardElement(_Element):
    """Справочная карточка: formulas and definitions."""

    kind: Literal["reference_card"]
    items: list[ReferenceItem]


class CodeTaskElement(_Element):
    """Задача с кодом (Т-§6.10): a ready code task of the label table (`K…`, from a
    `task_generation` request with a language), shown as it is; the learner solves it in the
    code editor and its tests check it. The teacher writes no task markup for it."""

    kind: Literal["code_task"]
    task: str


class CodeSandboxElement(_Element):
    """Песочница для кода (teacher@5, owner decision 2026-11-26): a code task the teacher writes
    in the turn; the editor opens at once. Its markup is in `p3_markup.tasks` (answer type
    `code`), as for any task element.

    - `mode`: `inline_patch` — complete a `TODO` or fix a bug, checked on the sample and by the
      teacher's review; `full_problem` — a whole block: hidden tests are built in the background.
    - `statement` — the statement (Markdown) the editor shows; `starter_code` — the code the
      learner starts from.
    - Python: `entry_point` — the function the tests call; `sample_args_json` — a JSON array of
      its arguments; `sample_expected_json` — the JSON of what it must return.
    - SQL: `sql_preset` — a ready database (`shop`, `university`), no `CREATE TABLE`;
      `sample_expected_json` — the JSON array of the rows the query must give; `entry_point` and
      `sample_args_json` are null. Column names are not compared.
    - `compare`: `exact` (for SQL — the order of rows counts), `unordered` (order does not count),
      `float_approx` (numbers within `epsilon`; null — the default tolerance)."""

    kind: Literal["code_sandbox"]
    mode: Literal["inline_patch", "full_problem"]
    language: Literal["python", "sql"]
    statement: str
    starter_code: str
    entry_point: str | None
    sql_preset: Literal["shop", "university"] | None
    sample_args_json: str | None
    sample_expected_json: str
    compare: Literal["exact", "unordered", "float_approx"]
    epsilon: float | None


# The artifact types a learner skill may be bound to (`artifacts.documents.ArtifactKind`).
type SkillArtifactType = Literal["notes", "cards", "cheatsheet"]


class SkillProposalElement(_Element):
    """Предложение скилла, only in a service turn of the built-in `/skill-creator` (owner decision
    2026-11-24): a draft of a learner skill the learner may edit and save from the browser
    (`POST /v1/learner-skills`). The model never writes the skill store (law 2); code settles the
    name and the text (`session.service_turns.settle_proposal`). `artifact_type` — the artifact it
    is bound to, or null."""

    kind: Literal["skill_proposal"]
    name: str
    text: str
    artifact_type: SkillArtifactType | None


type Element = Annotated[
    ChoiceElement
    | WhyChoiceElement
    | ShortAnswerElement
    | TaskElement
    | ScaffoldElement
    | PredictionElement
    | FreeAnswerElement
    | PairElement
    | CardSeriesElement
    | TimedQuizElement
    | VisualizationElement
    | MaterialLinkElement
    | ReferenceCardElement
    | CodeTaskElement
    | CodeSandboxElement
    | SkillProposalElement,
    Field(discriminator="kind"),
]
