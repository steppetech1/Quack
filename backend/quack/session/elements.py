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
    {"choice", "why_choice", "short_answer", "task", "scaffold", "prediction", "free_answer"}
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
    | ReferenceCardElement,
    Field(discriminator="kind"),
]
