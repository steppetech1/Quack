"""`ops status` (ТЗ Фазы 3 WP8.4, WP11.5, Д-§9.1; ТЗ Ф4 WP9.5, WP10.4): one JSON object about the
running system, read from the schema of Phase 4 through `Db` (D-C-181).

Exit code 0 when Postgres answers, else 2. Neo4j and Redis are reported, not required: when one
of them is down its values are `null` and `dependencies` shows `"error"`. No thresholds and no
alerts here (`ops alerts`, Д-§9.3). Dependency errors are logged at debug level only: stdout
carries the one JSON object.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, LiteralString

from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.config import Settings
from quack.db import Db
from quack.db.neo4j import read_session
from quack.db.redis import llm_circuit_key, llm_pressure_key
from quack.logging import get_logger

log = get_logger(__name__)

_PROJECTOR = (
    "SELECT COUNT(*) AS total, "
    "COUNT(*) FILTER (WHERE l.seq > COALESCE(o.seq, 0)) AS lagging, "
    "COALESCE(MAX(GREATEST(l.seq - COALESCE(o.seq, 0), 0)), 0) AS max_lag "
    "FROM core.learners l LEFT JOIN ops.projector_offsets o ON o.learner_id = l.learner_id"
)
_DISPATCHER = (
    "SELECT COUNT(*) FILTER (WHERE l.seq > COALESCE(d.last_dispatched_seq, 0)) AS lagging, "
    "COALESCE(MAX(GREATEST(l.seq - COALESCE(d.last_dispatched_seq, 0), 0)), 0) AS max_lag "
    "FROM core.learners l "
    "LEFT JOIN ops.dispatcher_offsets d ON d.learner_id = l.learner_id"
)
# A stopped stream is a row of `ops.projector_failures`; `ops resume` removes it (Д-§8.7).
_STOPPED = "SELECT COUNT(*) AS n FROM ops.projector_failures"
_BY_STATUS = "SELECT status, COUNT(*) AS n FROM ops.jobs GROUP BY status"
_OLDEST_QUEUED = (
    "SELECT EXTRACT(EPOCH FROM (now() - MIN(run_at)))::float8 AS age FROM ops.jobs "
    "WHERE status = 'queued' AND run_at <= now()"
)
# The readiness condition of the claim (Т-§13.1, `queue_p4._CANDIDATE`), negated.
_WAITING = (
    "SELECT COUNT(*) AS n FROM ops.jobs q WHERE q.status = 'queued' AND q.required_seq IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM ops.projector_offsets o WHERE o.learner_id = q.learner_id "
    "AND o.seq >= q.required_seq)"
)
# `ops.jobs` keeps no time of the end; the last pulse of a finished job is the nearest to it
# (a job shorter than the pulse interval has its start), D-C-181.
_FINISHED_HOUR = (
    "SELECT COUNT(*) FILTER (WHERE status = 'succeeded') AS done, "
    "COUNT(*) FILTER (WHERE status = 'failed') AS failed FROM ops.jobs "
    "WHERE status IN ('succeeded', 'failed') AND heartbeat_at >= now() - interval '1 hour'"
)
_LLM = (
    "SELECT "
    "COALESCE(SUM(input_tokens + output_tokens) FILTER "
    "(WHERE created_at >= now() - interval '1 hour'), 0) AS hour, "
    "COALESCE(SUM(input_tokens + output_tokens), 0) AS day, "
    "COUNT(*) FILTER (WHERE status <> 'ok' AND created_at >= now() - interval '1 hour') AS errors, "
    "COALESCE(SUM(cost_usd) FILTER "
    "(WHERE created_at >= now() - interval '1 hour'), 0)::float8 AS cost_hour, "
    "COALESCE(SUM(cost_usd), 0)::float8 AS cost_day "
    "FROM ops.llm_usage WHERE created_at >= now() - interval '1 day'"
)


async def _row(db: Db, query: LiteralString) -> dict[str, Any]:
    row = await db.fetch_one(query)
    assert row is not None  # an aggregate without GROUP BY always yields a row
    return row


async def _postgres_part(db: Db) -> dict[str, Any]:
    projector = await _row(db, _PROJECTOR)
    dispatcher = await _row(db, _DISPATCHER)
    by_status = {row["status"]: int(row["n"]) for row in await db.fetch_all(_BY_STATUS)}
    oldest = (await _row(db, _OLDEST_QUEUED))["age"]
    finished = await _row(db, _FINISHED_HOUR)
    llm = await _row(db, _LLM)
    done, failed = int(finished["done"]), int(finished["failed"])
    return {
        "projector": {
            "learners_total": int(projector["total"]),
            "learners_lagging": int(projector["lagging"]),
            "max_lag_events": int(projector["max_lag"]),
            "stopped_streams": int((await _row(db, _STOPPED))["n"]),
        },
        "dispatcher": {
            "learners_lagging": int(dispatcher["lagging"]),
            "max_lag_events": int(dispatcher["max_lag"]),
        },
        "jobs": {
            "by_status": by_status,
            "oldest_queued_age_s": None if oldest is None else float(oldest),
            "waiting_for_projection": int((await _row(db, _WAITING))["n"]),
            "finished_last_hour": done,
            "failed_last_hour": failed,
            "failed_share_last_hour": None if done + failed == 0 else failed / (done + failed),
        },
        "llm": {
            "tokens_last_hour": int(llm["hour"]),
            "tokens_last_day": int(llm["day"]),
            "errors_last_hour": int(llm["errors"]),
            "cost_last_hour_usd": float(llm["cost_hour"]),
            "cost_last_day_usd": float(llm["cost_day"]),
        },
    }


async def _neo4j_ok(driver: AsyncDriver | None) -> bool:
    if driver is None:
        return False
    try:
        async with read_session(driver) as session:
            result = await session.run("RETURN 1 AS ok")
            await result.consume()
        return True
    except Exception as exc:
        log.debug("ops_status_neo4j_error", error=repr(exc))
        return False


async def _models_with(redis: Redis, prefix: str) -> list[str]:
    names: list[str] = []
    cursor = 0
    while True:
        cursor, batch = await redis.scan(cursor=cursor, match=f"{prefix}*")
        for key in batch:
            name = key.decode() if isinstance(key, bytes) else str(key)
            if name != prefix:
                names.append(name.removeprefix(prefix))
        if cursor == 0:
            return sorted(set(names))


async def _redis_part(redis: Redis | None) -> tuple[bool, list[str] | None, list[str] | None]:
    if redis is None:
        return False, None, None
    try:
        await redis.ping()
        pressure = await _models_with(redis, llm_pressure_key(""))
        circuit = await _models_with(redis, llm_circuit_key(""))
    except Exception as exc:
        log.debug("ops_status_redis_error", error=repr(exc))
        return False, None, None
    return True, pressure, circuit


async def collect_status(
    settings: Settings,
    db: Db,
    neo4j: AsyncDriver | None,
    redis: Redis | None,
) -> tuple[dict[str, Any], int]:
    """The report of WP8.4 and the exit code (0 — Postgres answers, 2 — it does not)."""
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "version": settings.quack_version,
    }
    try:
        part: dict[str, Any] | None = await _postgres_part(db)
    except Exception as exc:
        log.debug("ops_status_postgres_error", error=repr(exc))
        part = None
    redis_ok, pressure, circuit = await _redis_part(redis)
    report["dependencies"] = {
        "postgres": "ok" if part is not None else "error",
        "neo4j": "ok" if await _neo4j_ok(neo4j) else "error",
        "redis": "ok" if redis_ok else "error",
    }
    empty: dict[str, Any] = {
        "projector": dict.fromkeys(
            ("learners_total", "learners_lagging", "max_lag_events", "stopped_streams")
        ),
        "dispatcher": dict.fromkeys(("learners_lagging", "max_lag_events")),
        "jobs": dict.fromkeys(
            (
                "by_status",
                "oldest_queued_age_s",
                "waiting_for_projection",
                "finished_last_hour",
                "failed_last_hour",
                "failed_share_last_hour",
            )
        ),
        "llm": dict.fromkeys(
            (
                "tokens_last_hour",
                "tokens_last_day",
                "errors_last_hour",
                "cost_last_hour_usd",
                "cost_last_day_usd",
            )
        ),
    }
    report.update(part if part is not None else empty)
    report["llm"]["pressure"] = pressure
    report["llm"]["circuit_open"] = circuit
    return report, 0 if part is not None else 2
