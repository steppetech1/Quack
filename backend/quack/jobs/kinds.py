"""Job kinds with priorities, lock and coalesce keys (spec §7.1), the kind catalog and the
LLM roles per kind (ТЗ Фазы 3 §6.5, seam S2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from quack.jobs.queue import JobDeferred, JobFailedPermanently, JobSpec

__all__ = [
    "JOB_KINDS",
    "KIND_LLM_ROLES",
    "JobDeferred",
    "JobFailedPermanently",
    "account_purge_deleted",
    "artifact_refresh_cheatsheet",
    "graph_curate",
    "session_checkpoint",
    "session_finish_artifacts",
    "session_sweep_inactive",
    "turn_extract",
]

# WP2.2: every kind with a registered handler; `workers` refuses to start on a mismatch (T-Q7).
JOB_KINDS: tuple[str, ...] = (
    "turn.extract",
    "session.checkpoint",
    "session.finish_artifacts",
    "artifact.refresh_cheatsheet",
    "graph.curate",
    "session.sweep_inactive",
    "account.purge_deleted",
)

# WP5.3/T-Q10: the roles whose models a kind calls; a kind waits while any of them is pressured.
# `session.checkpoint` calls no model since WP13 (deterministic summary, law 10).
KIND_LLM_ROLES: dict[str, tuple[str, ...]] = {
    "turn.extract": ("extractor",),
    "session.checkpoint": (),
    "session.finish_artifacts": ("artifacts",),
    "artifact.refresh_cheatsheet": ("artifacts",),
    "graph.curate": ("curator",),
    "session.sweep_inactive": (),
    "account.purge_deleted": (),
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


def graph_curate(
    learner_id: UUID, domain_id: UUID, payload: dict[str, Any], *, required_seq: int | None
) -> JobSpec:
    return JobSpec(
        kind="graph.curate",
        priority=80,
        lock_key=f"learner:{learner_id}:curate",
        coalesce_key=f"curate:{domain_id}",
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


def account_purge_deleted(payload: dict[str, Any] | None = None) -> JobSpec:
    return JobSpec(
        kind="account.purge_deleted",
        priority=95,
        lock_key="global:purge_deleted",
        coalesce_key="global:purge_deleted",
        payload=payload or {},
    )
