"""Labels in the context and the answer of teacher@1 (WP14.1, Т-§4.5)."""

from __future__ import annotations

import re
from uuid import uuid4

from quack.config import Settings
from quack.practice.evaluation import TaskToCheck
from quack.session.schemas import ConceptExplainedDraft, TeacherOutput
from quack.session.teacher_labels import (
    build_teacher_labels,
    misconceptions_view,
    offered_tasks,
    resolve_teacher_output,
    skills_view,
    tasks_view,
    turns_view,
)

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _task(topic: object) -> TaskToCheck:
    return TaskToCheck(
        task_id=uuid4(),
        kind="math",
        prompt_text="Find d/dx x^3",
        reference_solution={},
        target_skills=[],
        target_misconceptions=[],
        difficulty=0.5,
        topic_id=topic,  # type: ignore[arg-type]  # any id works for the view
        family_id="fam",
    )


def test_views_carry_labels_not_ids_and_answer_resolves() -> None:
    topic, skill_a, skill_b, misc = uuid4(), uuid4(), uuid4(), uuid4()
    skills = [
        {"skill_id": str(skill_a), "id": str(skill_a), "learnerId": "x", "name": "Power rule"},
        {"skill_id": str(skill_b), "id": str(skill_b), "name": "Chain rule"},
    ]
    miscs = [{"misconception_id": str(misc), "skill_id": str(skill_a), "name": "drops n"}]
    tasks = [_task(topic) for _ in range(7)]
    settings = Settings()
    labels = build_teacher_labels(
        settings,
        topic_id=topic,
        skills_slice=skills,
        misconceptions=miscs,
        available_tasks=tasks,
        submitted_task=None,
    )
    offered = offered_tasks(labels, tasks)
    assert len(offered) == settings.label_max_tasks
    views = [
        skills_view(labels, skills),
        misconceptions_view(labels, miscs),
        tasks_view(labels, offered),
        turns_view(labels, [{"turn_id": str(uuid4()), "related_task_id": str(tasks[0].task_id)}]),
    ]
    assert not UUID_RE.search(repr(views))
    assert views[0][0]["skill"] == "S1" and views[1][0]["skill"] == "S1"
    assert views[3][0]["related_task"] == "K1"

    output = TeacherOutput(
        reply_text="ok",
        pedagogical_move="assign_task",
        focus_skill_labels=["S2", "S9"],
        concept_explained=ConceptExplainedDraft(
            topic_label="T1", explained_skill_labels=["S1", "S7"], depth=0.5, summary="s"
        ),
        assign_task_label="K2",
    )
    decision = resolve_teacher_output(output, labels)
    assert decision.focus_skill_ids == [skill_b]  # S9 is unknown: dropped, turn goes on
    assert decision.assign_task_id == tasks[1].task_id
    assert decision.concept is not None
    assert (decision.concept.topic_id, decision.concept.skill_ids) == (topic, [skill_a])

    unknown = resolve_teacher_output(
        output.model_copy(
            update={
                "assign_task_label": "K6",  # beyond label_max_tasks: never offered
                "concept_explained": ConceptExplainedDraft(
                    topic_label="T2", explained_skill_labels=[], depth=0.1, summary="s"
                ),
            }
        ),
        labels,
    )
    assert unknown.assign_task_id is None
    assert unknown.concept is None
