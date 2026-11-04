"""Answer specification and the learner's answer (Т-§5.2, Т-§6, Т-§22.7; ТЗ Ф4 WP11.1, seam S7).

One model shared by the task generator, the teacher (`p3_markup.tasks`), cards and the canvas.
The model writes it, so it is strict-schema friendly: no free dicts, optional fields are
nullable (Т-§11.1). Every expression carries two fields, `latex` for display and `sympy` for
checking (Т-§22.7). Field-level decisions — D-B-107.

The models are event payload parts, so they live in the core (`quack.events.values`, seam S3,
Q-A-1 variant (a)); this module re-exports them and keeps the check speeds of Т-§6.1.
"""

from __future__ import annotations

from quack.events.values import (
    AnswerOption,
    AnswerSpec,
    AnswerType,
    Assumption,
    CompareMode,
    FormTarget,
    LearnerAnswer,
    MathValue,
    Variable,
)

__all__ = [
    "FAST_TYPES",
    "INSTANT_TYPES",
    "SLOW_TYPES",
    "AnswerOption",
    "AnswerSpec",
    "AnswerType",
    "Assumption",
    "CompareMode",
    "FormTarget",
    "LearnerAnswer",
    "MathValue",
    "Variable",
]

# Т-§6.1: which speed checks a type.
INSTANT_TYPES: frozenset[str] = frozenset({"choice", "number", "short_text", "set", "vector"})
FAST_TYPES: frozenset[str] = frozenset({"expression", "quantity"})
SLOW_TYPES: frozenset[str] = frozenset({"proof", "free_text", "drawing"})
