"""Labels in the context and the answer of `teacher@1` (WP14.1, Т-§4.5).

The teacher sees the session topic as `T1`, the skills of the slice as `S…`, their known
misconceptions as `M…` and the offered tasks as `K…`; no id reaches the prompt. Code turns
the labels of the answer back into ids. An unknown label is a labelling error: the element
is dropped with a log record and the rest of the answer is applied; the turn never fails
(law 9). Before that the answer goes back to the model once with the unknown labels named
(`teacher_label_errors`, the gateway repair path of WP5).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from quack.config import Settings
from quack.llm.labels import LabelKind, LabelMap, UnknownLabelError
from quack.logging import get_logger
from quack.practice.evaluation import AnswerVerdict, TaskToCheck
from quack.session.schemas import TeacherOutput

log = get_logger(__name__)

# Keys of node properties and views that carry ids; they never reach the prompt.
_ID_KEYS = frozenset({"id", "seq"})


def _is_id_key(key: str) -> bool:
    return key in _ID_KEYS or key.lower().endswith("id")


def _without_ids(props: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if not _is_id_key(k)}


def build_teacher_labels(
    settings: Settings,
    *,
    topic_id: UUID | None,
    skills_slice: Sequence[Mapping[str, Any]],
    misconceptions: Sequence[Mapping[str, Any]],
    available_tasks: Sequence[TaskToCheck],
    submitted_task: TaskToCheck | None,
) -> LabelMap:
    """Labels in the order of the context: offered tasks first, the answered task after."""
    tasks = [t.task_id for t in available_tasks]
    if submitted_task is not None:
        tasks.append(submitted_task.task_id)
    return LabelMap.build(
        settings,
        skills=[UUID(str(s["skill_id"])) for s in skills_slice],
        topics=[topic_id] if topic_id is not None else [],
        tasks=tasks,
        misconceptions=[UUID(str(m["misconception_id"])) for m in misconceptions],
    )


def _label(labels: LabelMap, kind: LabelKind, value: Any) -> str | None:
    return None if value is None else labels.label(kind, UUID(str(value)))


def topic_line(labels: LabelMap, topic_id: UUID | None, topic_name: str) -> str:
    label = _label(labels, "topic", topic_id)
    return f"{label}: {topic_name}" if label is not None else topic_name


def skills_view(
    labels: LabelMap, skills_slice: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for skill in skills_slice:
        label = _label(labels, "skill", skill["skill_id"])
        if label is not None:
            out.append({"skill": label, **_without_ids(skill)})
    return out


def misconceptions_view(
    labels: LabelMap, misconceptions: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in misconceptions:
        label = _label(labels, "misconception", item["misconception_id"])
        if label is not None:
            skill = _label(labels, "skill", item.get("skill_id"))
            out.append({"misconception": label, "skill": skill, **_without_ids(item)})
    return out


def offered_tasks(labels: LabelMap, available: Sequence[TaskToCheck]) -> list[TaskToCheck]:
    """Tasks the teacher may assign: those that got a label (`label_max_tasks`)."""
    return [t for t in available if labels.label("task", t.task_id) is not None]


def tasks_view(labels: LabelMap, offered: Sequence[TaskToCheck]) -> list[dict[str, Any]]:
    return [
        {
            "task": labels.label("task", t.task_id),
            "family_id": t.family_id,
            "kind": t.kind,
            "difficulty": t.difficulty,
            "prompt_text": t.prompt_text,
        }
        for t in offered
    ]


def turns_view(labels: LabelMap, turns: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for turn in turns:
        view = _without_ids(turn)
        task = _label(labels, "task", turn.get("related_task_id"))
        if task is not None:
            view["related_task"] = task
        out.append(view)
    return out


def evaluation_view(labels: LabelMap, task: TaskToCheck, verdict: AnswerVerdict) -> str:
    skill_scores = []
    for score in verdict.skill_scores:
        label = _label(labels, "skill", score.get("skill_id"))
        if label is not None:
            skill_scores.append({"skill": label, **_without_ids(score)})
    return json.dumps(
        {
            "task": labels.label("task", task.task_id),
            "evaluator": verdict.evaluator,
            "is_correct": verdict.is_correct,
            "score": verdict.score,
            "independence": verdict.independence,
            "feedback": verdict.feedback,
            "skill_scores": skill_scores,
        },
        ensure_ascii=False,
    )


@dataclass(frozen=True)
class ConceptDecision:
    topic_id: UUID
    skill_ids: list[UUID]
    depth: float
    summary: str


@dataclass(frozen=True)
class TeacherDecision:
    """The answer of the teacher with labels resolved to ids."""

    focus_skill_ids: list[UUID]
    assign_task_id: UUID | None
    concept: ConceptDecision | None


def _resolve(labels: LabelMap, kind: LabelKind, label: str) -> UUID | None:
    try:
        return labels.resolve(kind, label.strip())
    except UnknownLabelError:
        log.warning("teacher_unknown_label", kind=kind, label=label)
        return None


def _resolve_all(labels: LabelMap, kind: LabelKind, values: Sequence[str]) -> list[UUID]:
    resolved = (_resolve(labels, kind, v) for v in values)
    return list(dict.fromkeys(i for i in resolved if i is not None))


def teacher_label_errors(output: TeacherOutput, labels: LabelMap) -> str | None:
    """The labels of `output` that the table of this call does not have, as the error text
    of the single repair call (WP14.1, T-M2); None when every label resolves."""
    used: list[tuple[LabelKind, str]] = [("skill", s) for s in output.focus_skill_labels]
    if output.assign_task_label is not None:
        used.append(("task", output.assign_task_label))
    drafted = output.concept_explained
    if drafted is not None:
        used.append(("topic", drafted.topic_label))
        used.extend(("skill", s) for s in drafted.explained_skill_labels)
    unknown: list[str] = []
    for kind, label in used:
        try:
            labels.resolve(kind, label.strip())
        except UnknownLabelError:
            unknown.append(f"{label} ({kind})")
    if not unknown:
        return None
    return (
        "unknown labels " + ", ".join(dict.fromkeys(unknown)) + "; use only the labels given in "
        "the input blocks of this turn"
    )


def resolve_teacher_output(output: TeacherOutput, labels: LabelMap) -> TeacherDecision:
    concept: ConceptDecision | None = None
    drafted = output.concept_explained
    if drafted is not None:
        topic_id = _resolve(labels, "topic", drafted.topic_label)
        if topic_id is not None:
            concept = ConceptDecision(
                topic_id=topic_id,
                skill_ids=_resolve_all(labels, "skill", drafted.explained_skill_labels),
                depth=float(drafted.depth),
                summary=drafted.summary,
            )
    assign = output.assign_task_label
    return TeacherDecision(
        focus_skill_ids=_resolve_all(labels, "skill", output.focus_skill_labels),
        assign_task_id=_resolve(labels, "task", assign) if assign is not None else None,
        concept=concept,
    )
