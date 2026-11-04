"""Spoiler guard of the teacher turn (ТЗ Фазы 3 WP15; Т-§12.1, Т-§12.4, П-4.9).

The answer of an active task (shown in the session, not closed, the solution not requested)
must not appear in the teacher reply. The check runs on the full reply before the first SSE
`token`; on a violation the turn goes back to the model, then code removes the sentence.

The answer is taken from `reference_solution` in the format of WP6.4 (`ReferenceSolution`):
`math` — `expression`, `choice` — `answer` (the text of the correct option), `open` — not
checked (its `sample_answer` is one possible answer, not the answer). Both sides are
normalized locally: lower case, `**` → `^`,
superscripts, no spaces around operators, no `*`. A number is compared by value; an
expression or an option text — as a whole token. Integers below `spoiler_ignore_abs_below`
in absolute value and one-letter answers are not checked (Т-§12.4).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from quack.config import Settings
from quack.practice.evaluation import TaskToCheck

_OPERATOR_SPACES = re.compile(r"\s*([+\-*/^=(),])\s*")
_SPACES = re.compile(r"\s+")
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_NUMBERS_IN_TEXT = re.compile(r"(?<![\w.^])(-?)(\d+(?:\.\d+)?)(?![\w^]|\.\d)")
# A sentence ends with `.`, `!` or `?` followed by spaces, or at a line break.
_SENTENCE_BREAK = re.compile(r"((?<=[.!?])\s+|\n+)")


def _normalize(value: str) -> str:
    out = value.lower().replace("**", "^").replace("²", "^2").replace("³", "^3")
    out = _OPERATOR_SPACES.sub(r"\1", out).replace("*", "")
    return _SPACES.sub(" ", out).strip()


def _answer(task: TaskToCheck) -> str | None:
    reference = task.reference_solution
    if task.kind == "math":
        value = reference.get("expression")
    elif task.kind == "choice":
        value = reference.get("answer")
    else:
        return None
    return _normalize(str(value)) if value else None


def _is_trivial(answer: str, settings: Settings) -> bool:
    if len(answer) == 1 and answer.isalpha():
        return True
    if re.fullmatch(r"-?\d+", answer):
        return abs(int(answer)) < settings.spoiler_ignore_abs_below
    return False


def answer_in_text(text: str, answer: str, settings: Settings) -> bool:
    """True if `text` contains `answer` (normalized); trivial answers are never spoilers."""
    answer = _normalize(answer)
    if not answer or _is_trivial(answer, settings):
        return False
    normalized = _normalize(text)
    if _NUMBER.match(answer):
        expected = float(answer)
        return any(
            float(sign + digits) == expected
            for sign, digits in _NUMBERS_IN_TEXT.findall(normalized)
        )
    token = re.compile(rf"(?<![\w.^]){re.escape(answer)}(?![\w^(])")
    return token.search(normalized) is not None


def spoiler_found(text: str, task: TaskToCheck, settings: Settings) -> bool:
    """True if `text` contains the answer of `task` by its `reference_solution`."""
    answer = _answer(task)
    return answer is not None and answer_in_text(text, answer, settings)


def remove_answer_sentences(text: str, answers: Sequence[str], settings: Settings) -> str:
    """`text` without the sentences that contain any of `answers` (Т-§12.1; the answers of the
    active tasks of `content.tasks`, ТЗ Ф4 WP13.5)."""
    parts = _SENTENCE_BREAK.split(text)
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        if any(answer_in_text(sentence, answer, settings) for answer in answers):
            continue
        kept.append(sentence)
        if index + 1 < len(parts):
            kept.append(parts[index + 1])
    return "".join(kept).strip()


def find_spoiler(text: str, tasks: Sequence[TaskToCheck], settings: Settings) -> TaskToCheck | None:
    """The first task whose answer is in `text`, or `None`."""
    return next((task for task in tasks if spoiler_found(text, task, settings)), None)


def remove_spoiler_sentences(text: str, tasks: Sequence[TaskToCheck], settings: Settings) -> str:
    """`text` without the sentences that contain the answer of any of `tasks` (Т-§12.1)."""
    parts = _SENTENCE_BREAK.split(text)
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        if find_spoiler(sentence, tasks, settings) is not None:
            continue
        kept.append(sentence)
        if index + 1 < len(parts):
            kept.append(parts[index + 1])
    return "".join(kept).strip()
