"""Task markup (Т-§5.2) and its code check (Т-§5.3); ТЗ Ф4 WP12.2–12.3, seam S7.

The teacher and the generator write markup with short labels (`S1`, `M2`, `K3`, Т-§4.5);
code translates them to ids before writing `task.created` / `turn.committed`. Field names
of the markup are those of Т-§5.2 (`methodNamed`, `variantOf`, `pairTaskId`) — they are
part of the model output contract. Decisions on the shape — D-B-107.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.practice.answer_spec import AnswerSpec

type TaskForm = Literal["multistep", "explain_why", "what_breaks"]

type Level = Annotated[int, Field(ge=1, le=6)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class NewSkill(_Strict):
    """Description of a new skill (Т-§4.4): name and the topic it belongs to in words."""

    name: str
    topic_hint: str


class TestedLevel(_Strict):
    """One pair of `tests`: a skill — label (`skill`) or a new one (`new_skill`) — and a
    level 1–6 (Т-§5.2). Exactly one of `skill` / `new_skill` is set."""

    skill: str | None = None
    new_skill: NewSkill | None = None
    level: Level


class OptionTrap(_Strict):
    """Т-§5.2 `options`: what a wrong option catches — a known misconception (label in
    `misconception`) or a hypothesis in words (`hypothesis`) — and whether it is the
    neighbouring idea (level 5, Т-§5.3). Keyed by the option id of `answer.options`."""

    option_id: str
    misconception: str | None = None
    hypothesis: str | None = None
    neighbor_idea: bool = False


class TaskPart(_Strict):
    """A part of a task with its own answer (Т-§6.2); `tests` null — inherits the task's."""

    part: str
    answer: AnswerSpec
    tests: list[TestedLevel] | None = None


class TaskMarkup(_Strict):
    """Т-§5.2: markup of every task the learner sees."""

    tests: list[TestedLevel]
    method_named: bool = Field(alias="methodNamed")
    variant_of: str | None = Field(default=None, alias="variantOf")
    answer: AnswerSpec
    options: list[OptionTrap] | None = None
    pair_task_id: str | None = Field(default=None, alias="pairTaskId")
    form: TaskForm | None = None
    parts: list[TaskPart] | None = None


type MarkupRule = Literal[
    "no_tests",
    "level_1_needs_options",
    "level_2_not_choice",
    "level_4_method_named",
    "level_5_needs_pair_or_neighbor",
    "level_6_needs_form",
    "choice_one_correct",
    "unknown_answer_type",
    "expression_unparsed",
    "quantity_without_unit",
    "unknown_label",
    "skill_label_and_new",
]


@dataclass(frozen=True)
class MarkupViolation:
    """One failed rule of Т-§5.3; `detail` says where (`tests[0]`, `answer.value`)."""

    rule: MarkupRule
    detail: str


def validate_markup(
    markup: TaskMarkup,
    *,
    skill_labels: Collection[str],
    misconception_labels: Collection[str],
    task_labels: Collection[str],
) -> list[MarkupViolation]:
    """Т-§5.3: form consistency of the markup; empty list — the task may be shown.

    Checks the level table (1 options, 2 not a choice, 4 `methodNamed = false`, 5 pair or
    neighbouring idea, 6 `form`), the common rules (a pair in `tests`, exactly one correct
    option, the answer spec parses: known type, expression parses by the grammar of
    Т-§6.5, a quantity has a unit) and that labels exist (Т-§12.4 «короткие метки
    существуют»). Code checks form, not truth (Т-§5.3).
    """
    raise NotImplementedError
