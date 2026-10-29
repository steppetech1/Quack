"""Job kinds of Phases 1–2 with priorities, lock and coalesce keys (spec §7.1)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quack.jobs.queue import JobSpec


def turn_extract(learner_id: UUID, session_id: UUID, payload: dict[str, Any]) -> JobSpec:
    return JobSpec(
        kind="turn.extract",
        priority=10,
        lock_key=f"session:{session_id}:extract",
        coalesce_key=None,
        learner_id=learner_id,
        payload=payload,
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
    learner_id: UUID, session_id: UUID, payload: dict[str, Any]
) -> JobSpec:
    return JobSpec(
        kind="session.finish_artifacts",
        priority=30,
        lock_key=f"session:{session_id}:finish",
        coalesce_key=f"finish:{session_id}",
        learner_id=learner_id,
        payload=payload,
    )


def artifact_refresh_cheatsheet(
    learner_id: UUID, topic_id: UUID, payload: dict[str, Any]
) -> JobSpec:
    return JobSpec(
        kind="artifact.refresh_cheatsheet",
        priority=40,
        lock_key=f"topic:{topic_id}:cheatsheet",
        coalesce_key=f"cheatsheet:{topic_id}",
        learner_id=learner_id,
        payload=payload,
    )


def graph_curate(learner_id: UUID, domain_id: UUID, payload: dict[str, Any]) -> JobSpec:
    return JobSpec(
        kind="graph.curate",
        priority=80,
        lock_key=f"learner:{learner_id}:curate",
        coalesce_key=f"curate:{domain_id}",
        learner_id=learner_id,
        payload=payload,
    )


def session_sweep_inactive(payload: dict[str, Any] | None = None) -> JobSpec:
    return JobSpec(
        kind="session.sweep_inactive",
        priority=90,
        lock_key="global:sweep_inactive",
        coalesce_key="global:sweep_inactive",
        payload=payload or {},
    )
