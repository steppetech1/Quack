"""Job kinds with priorities, lock and coalesce keys (spec §7.1), the kind catalog and the
LLM roles per kind (ТЗ Фазы 3 §6.5, seam S2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quack.jobs.types import JobDeferred, JobFailedPermanently, JobSpec

__all__ = [
    "JOB_KINDS",
    "KIND_LLM_ROLES",
    "JobDeferred",
    "JobFailedPermanently",
    "artifact_refresh_cheatsheet",
    "session_checkpoint",
    "session_finish_artifacts",
    "session_sweep_inactive",
    "turn_extract",
]

# WP2.2: every kind with a registered handler; `workers` refuses to start on a mismatch (T-Q7).
JOB_KINDS: tuple[str, ...] = (
    "session.sweep_inactive",
    "artifact.topic_refresh",
    "artifact.cheatsheet",
    "agent.viz",
    "photo.pipeline",
    "extract.episode",
    "misconception.describe",
    "analyst.run",
    "analyst.deep",
    "check.symbolic",
    "task.check",
    "check.model",
    "today.prepare",
    "graph.hygiene",
    "deletions.reconcile",
    "manifests.cleanup",
    "exports.cleanup",
    "calibration.weekly",
    "task.solution_space",
    "agent.task_generation",
    "agent.solution_space",
    "check.known_claim",
    "agent.illustration",
    "material.parse",
    "agent.topic_split",
    "embedding.compute",
    "second_opinion.diagnosis",
    "canvas.rediagnose",
)

# WP5.3/T-Q10: the roles whose models a kind calls; a kind waits while any of them is pressured.
KIND_LLM_ROLES: dict[str, tuple[str, ...]] = {
    "session.sweep_inactive": (),
    "artifact.topic_refresh": ("artifact_writer",),
    "artifact.cheatsheet": ("artifact_writer",),
    "agent.viz": ("viz_builder", "viz_checker"),
    "photo.pipeline": ("recognizer", "segmenter"),
    "extract.episode": ("extractor",),
    "misconception.describe": ("misconception_describer",),
    "analyst.run": ("analyst",),
    "analyst.deep": ("analyst_deep",),
    "check.symbolic": (),
    "task.check": ("task_checker",),
    "check.model": ("verdict_proof", "verdict_free"),
    "today.prepare": ("today_planner",),
    "graph.hygiene": ("builder",),
    "deletions.reconcile": (),
    "manifests.cleanup": (),
    "exports.cleanup": (),
    "calibration.weekly": (),
    "task.solution_space": ("solution_space",),
    "agent.task_generation": ("task_generator", "task_arbiter"),
    "agent.solution_space": ("solution_space",),
    "check.known_claim": ("task_generator",),
    "agent.illustration": ("illustrator",),
    "material.parse": ("material_parser",),
    "agent.topic_split": ("builder",),
    "embedding.compute": (),
    "second_opinion.diagnosis": ("second_opinion",),
    "canvas.rediagnose": ("canvas_diagnoser",),
}


def turn_extract(
    learner_id: UUID, session_id: UUID, payload: dict[str, Any], *, required_seq: int | None
) -> JobSpec:
    return JobSpec(
        kind="turn.extract",
        priority=10,
        lock_key=f"session:{session_id}:extract",
        coalesce_key=None,
        learner_id=learner_id,
        payload=payload,
        required_seq=required_seq,
    )


def session_checkpoint(learner_id: UUID, session_id: UUID, payload: dict[str, Any]) -> JobSpec:
    return JobSpec(
        kind="session.checkpoint",
        priority=20,
        lock_key=f"session:{session_id}:checkpoint",
        coalesce_key=f"checkpoint:{session_id}",
        learner_id=learner_id,
        payload=payload,
    )


def session_finish_artifacts(
    learner_id: UUID, session_id: UUID, payload: dict[str, Any], *, required_seq: int | None
) -> JobSpec:
    return JobSpec(
        kind="session.finish_artifacts",
        priority=30,
        lock_key=f"session:{session_id}:finish",
        coalesce_key=f"finish:{session_id}",
        learner_id=learner_id,
        payload=payload,
        required_seq=required_seq,
    )


def artifact_refresh_cheatsheet(
    learner_id: UUID, topic_id: UUID, payload: dict[str, Any], *, required_seq: int | None
) -> JobSpec:
    return JobSpec(
        kind="artifact.refresh_cheatsheet",
        priority=40,
        lock_key=f"topic:{topic_id}:cheatsheet",
        coalesce_key=f"cheatsheet:{topic_id}",
        learner_id=learner_id,
        payload=payload,
        required_seq=required_seq,
    )


def session_sweep_inactive(payload: dict[str, Any] | None = None) -> JobSpec:
    return JobSpec(
        kind="session.sweep_inactive",
        priority=90,
        lock_key="global:sweep_inactive",
        coalesce_key="global:sweep_inactive",
        payload=payload or {},
    )
