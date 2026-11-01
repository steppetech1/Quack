"""Task markup (Т-§5.2) and its code check (Т-§5.3); ТЗ Ф4 WP12.2–12.3, seam S7.

The teacher and the generator write markup with short labels (`S1`, `M2`, `K3`, Т-§4.5);
code translates them to ids before writing `task.created` / `turn.committed`. Field names
of the markup are those of Т-§5.2 (`methodNamed`, `variantOf`, `pairTaskId`) — they are
part of the model output contract. Decisions on the shape — D-B-107.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.practice.answer_spec import AnswerSpec
from quack.practice.grammar import grammar_ok

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
    found: list[MarkupViolation] = []

    def add(rule: MarkupRule, detail: str) -> None:
        found.append(MarkupViolation(rule, detail))

    if not markup.tests:
        add("no_tests", "tests")
    answers = [("answer", markup.answer)] + [
        (f"parts[{i}].answer", part.answer) for i, part in enumerate(markup.parts or [])
    ]
    tests = [("tests", t) for t in markup.tests] + [
        (f"parts[{i}].tests", t)
        for i, part in enumerate(markup.parts or [])
        for t in part.tests or []
    ]
    for where, tested in tests:
        if tested.skill is not None and tested.new_skill is not None:
            add("skill_label_and_new", where)
        elif tested.skill is not None and tested.skill not in skill_labels:
            add("unknown_label", f"{where}: {tested.skill}")
    for where, spec in answers:
        _check_answer(spec, where, add)

    levels = {t.level for _, t in tests}
    is_choice = markup.answer.type == "choice"
    neighbor = any(o.neighbor_idea for o in markup.options or [])
    if 1 in levels and not is_choice:
        add("level_1_needs_options", "answer.type")
    if 2 in levels and is_choice:
        add("level_2_not_choice", "answer.type")
    if 4 in levels and markup.method_named:
        add("level_4_method_named", "methodNamed")
    if 5 in levels and markup.pair_task_id is None and not (is_choice and neighbor):
        add("level_5_needs_pair_or_neighbor", "pairTaskId / options")
    if 6 in levels and markup.form is None:
        add("level_6_needs_form", "form")

    if markup.variant_of is not None and markup.variant_of not in task_labels:
        add("unknown_label", f"variantOf: {markup.variant_of}")
    if markup.pair_task_id is not None and markup.pair_task_id not in task_labels:
        add("unknown_label", f"pairTaskId: {markup.pair_task_id}")
    option_ids = {o.option_id for o in markup.answer.options or []}
    for i, trap in enumerate(markup.options or []):
        if trap.option_id not in option_ids:
            add("unknown_label", f"options[{i}].option_id: {trap.option_id}")
        if trap.misconception is not None and trap.misconception not in misconception_labels:
            add("unknown_label", f"options[{i}].misconception: {trap.misconception}")
    return found


_TYPES_WITH_VALUE = frozenset({"number", "expression", "quantity"})


def _check_answer(spec: AnswerSpec, where: str, add: Callable[[MarkupRule, str], None]) -> None:
    """Common rules of Т-§5.3 for one answer spec: exactly one correct option, the spec
    parses (a value where the type needs one, expressions pass the grammar of Т-§6.5, a
    quantity has a unit)."""
    if spec.type == "choice":
        correct = sum(o.correct for o in spec.options or [])
        if correct != 1:
            add("choice_one_correct", f"{where}.options")
        return
    if spec.type in _TYPES_WITH_VALUE and spec.value is None:
        add("unknown_answer_type", f"{where}.value")
    if spec.type in ("set", "vector") and not spec.items:
        add("unknown_answer_type", f"{where}.items")
    if spec.type == "short_text" and not spec.accepted:
        add("unknown_answer_type", f"{where}.accepted")
    values = [spec.value] if spec.value is not None else []
    for value in [*values, *(spec.items or [])]:
        if not grammar_ok(value.sympy):
            add("expression_unparsed", f"{where}.value")
    if spec.type == "quantity" and not spec.unit:
        add("quantity_without_unit", f"{where}.unit")
