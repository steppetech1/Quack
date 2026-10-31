"""A `core.task_bank` row from the answer of `task_gen@1` (D-B-010).

The model writes the problem in the contract of its prompt; code decides everything the
bank keeps: the family, the difficulty, the target skill, and `reference_solution` in the
keys the readers use today (`sympy`/`check_mode`, `option`, `answer`, `hints`). The move to
the keys of WP6.4 is one change of this module and of the readers (D-B-001).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from quack.compute_client.client import MathMode
from quack.practice.schemas import AnswerType, TaskGenOutput

# Answer types allowed for each task kind of the bank, and the compute mode of math types.
KIND_ANSWER_TYPES: dict[str, frozenset[AnswerType]] = {
    "math": frozenset({"number", "expression", "set", "vector", "quantity"}),
    "choice": frozenset({"choice"}),
    "open": frozenset({"proof", "free_text", "number", "expression", "quantity"}),
    "code": frozenset({"free_text"}),
}
MATH_CHECK_MODES: dict[AnswerType, MathMode] = {
    "number": "numeric_tol",
    "quantity": "numeric_tol",
    "expression": "expression_equiv",
    "set": "equation_roots",
    "vector": "matrix_eq",
}
GENERATED_FAMILY_PREFIX = "gen-"


class GeneratedTaskError(ValueError):
    """The answer of `task_gen@1` does not fit the requested kind; practice falls back to
    the bank as on an LLM error."""


@dataclass(frozen=True)
class GeneratedTask:
    family_id: str
    prompt_text: str
    reference_solution: dict[str, Any]
    target_misconceptions: list[str]


def generated_family_id(task_id: UUID) -> str:
    """Every generated task is its own family: the model sees no task labels to name a
    source problem in `variant_of`."""
    return f"{GENERATED_FAMILY_PREFIX}{task_id.hex[:12]}"


def _label_name(label: str | None, names: Sequence[str]) -> str | None:
    if label is None or not label.startswith("M") or not label[1:].isdigit():
        return None
    index = int(label[1:])
    return names[index - 1] if 1 <= index <= len(names) else None


def build_generated_task(
    output: TaskGenOutput, kind: str, task_id: UUID, misconception_names: Sequence[str]
) -> GeneratedTask:
    """The bank row of `output` for a task of `kind`; `GeneratedTaskError` when the answer
    type does not fit the kind or a choice task has no single correct option."""
    answer = output.answer
    if answer.type not in KIND_ANSWER_TYPES.get(kind, frozenset()):
        raise GeneratedTaskError(f"answer type {answer.type!r} does not fit kind {kind!r}")
    hints = [h for h in output.hints if h]
    reference: dict[str, Any]
    prompt_text = output.statement
    if kind == "choice":
        correct = [o for o in output.options if o.correct]
        if len(correct) != 1 or correct[0].label != answer.value:
            raise GeneratedTaskError("a choice task needs exactly one correct option")
        prompt_text += "\n\n" + "\n".join(f"{o.label}. {o.text}" for o in output.options)
        reference = {
            "option": answer.value,
            "answer": correct[0].text,
            "options": [o.label for o in output.options],
        }
    elif kind == "math":
        reference = {"sympy": answer.value, "check_mode": MATH_CHECK_MODES[answer.type]}
    else:
        reference = {"answer": answer.value}
    reference["solution"] = output.solution
    if answer.units is not None:
        reference["units"] = answer.units
    reference["hints"] = hints
    caught = (_label_name(o.catches, misconception_names) for o in output.options)
    return GeneratedTask(
        family_id=generated_family_id(task_id),
        prompt_text=prompt_text,
        reference_solution=reference,
        target_misconceptions=sorted({name for name in caught if name is not None}),
    )
