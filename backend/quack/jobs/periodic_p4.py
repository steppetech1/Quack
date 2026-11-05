"""Periodic jobs of Phase 4 (ТЗ Ф4 WP7.5; Т-§13.1): the key is tied to the period.

`job_key` names the period (`deletions-reconcile:{date}`, `calibration:{year-week}`), and the
queue drops a repeat of a `job_key` (`enqueue` is idempotent), so any number of `workers` ticks
put exactly one job per period (T-D9). The period of `session.sweep_inactive` is
`session_sweep_interval_s` (`sweep-inactive:{unix time // interval}`, D-A-211);
`dispatcher.episode_idle` runs inside the dispatcher (`DispatcherP4.close_idle_episodes`).
Times are UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime

from redis.asyncio import Redis

from quack.db import Db
from quack.jobs.queue_p4 import enqueue
from quack.jobs.rules_p4 import JobPlan


def periodic_plans(now: datetime, *, sweep_interval_s: int = 60) -> list[JobPlan]:
    """The jobs of the periods `now` falls in (the table of WP7.5)."""
    at = now.astimezone(UTC)
    day = at.date().isoformat()
    year, week, _ = at.isocalendar()
    keys = (
        ("session.sweep_inactive", f"sweep-inactive:{int(at.timestamp()) // sweep_interval_s}"),
        ("deletions.reconcile", f"deletions-reconcile:{day}"),
        ("manifests.cleanup", f"manifests-cleanup:{day}"),
        ("calibration.weekly", f"calibration:{year}-{week:02d}"),
        ("exports.cleanup", f"exports-cleanup:{day}"),
    )
    return [
        JobPlan(
            kind=kind,
            job_key=key,
            priority_class="P3",
            learner_id=None,
            lock_key=kind,
            payload={"period": key.split(":", 1)[1]},
        )
        for kind, key in keys
    ]


async def schedule_periodic(
    db: Db, now: datetime, *, redis: Redis | None = None, sweep_interval_s: int = 60
) -> int:
    """Put the jobs of the current periods; returns how many were new."""
    inserted = 0
    for plan in periodic_plans(now, sweep_interval_s=sweep_interval_s):
        inserted += await enqueue(db, plan, redis=redis) == "inserted"
    return inserted
