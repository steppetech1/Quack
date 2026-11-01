"""Entry point: python -m quack.cli api | worker | ops <command> (Д-§5.1, Д-§6)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import structlog
from sqlalchemy import text

from quack.config import Settings, get_settings
from quack.db.neo4j import create_driver, verify_schema
from quack.db.postgres import create_engine, create_session_factory
from quack.db.redis import create_redis
from quack.graph.ids import CURRENT_RULES_VERSION
from quack.graph.neo4j_store import find_learners_with_rules
from quack.graph.rebuild import rebuild_learner
from quack.graph.worker import ProjectorWorker
from quack.jobs.dispatcher import Dispatcher, supervise
from quack.jobs.periodic import PeriodicScheduler
from quack.jobs.runner import JobRunner
from quack.logging import configure_logging, get_logger
from quack.ops.commands import migrate, purge_deleted, replay_dlq
from quack.ops.registry import can_rollback, seen_pairs, write_event_registry
from quack.ops.seed import SeedRefusedError, seed_learners, write_learners
from quack.ops.status import collect_status
from quack.runtime import (
    JobHandlersMismatchError,
    check_handlers_complete,
    close_job_context,
    close_projector_context,
    create_job_context,
    create_projector_context,
    register_all_handlers,
)

log = get_logger(__name__)

type ProcessRole = Literal["api", "workers", "projector", "ops"]

# WP8.1, §4.3: the `process_role` each command runs with; `worker` is projector + workers.
PROCESS_ROLES: dict[str, ProcessRole] = {
    "api": "api",
    "projector": "projector",
    "workers": "workers",
    "worker": "workers",
    "ops": "ops",
}


def process_settings(settings: Settings, role: ProcessRole) -> Settings:
    """Set the role before any resource is created (§4.3) and bind `process` and `version` to
    every log line of this process (Д-§9.1)."""
    configured = settings.model_copy(update={"process_role": role})
    structlog.contextvars.bind_contextvars(process=role, version=configured.quack_version)
    return configured


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m quack.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api", help="run the HTTP API (uvicorn)")
    sub.add_parser("projector", help="run the projector and the log dispatcher")
    sub.add_parser("workers", help="run the job executors and the periodic scheduler")
    sub.add_parser("worker", help="projector + workers in one process (development only)")
    ops = sub.add_parser("ops", help="operational commands")
    ops_sub = ops.add_subparsers(dest="op", required=True)
    ops_sub.add_parser("migrate", help="alembic upgrade head")
    ops_sub.add_parser("verify-schema", help="create/verify Neo4j constraints and indexes")
    rebuild = ops_sub.add_parser("rebuild", help="rebuild learner projections from the log")
    target = rebuild.add_mutually_exclusive_group(required=False)
    target.add_argument("--learner", help="learner id")
    target.add_argument("--stale-rules", action="store_true", help="learners on other rules@N")
    target.add_argument("--all", action="store_true", help="every learner")
    replay = ops_sub.add_parser("replay-dlq", help="replay DLQ entries")
    replay.add_argument("--id", action="append", dest="ids", help="DLQ entry id (repeatable)")
    ops_sub.add_parser("purge-deleted", help="physically delete learners with tombstones")
    ops_sub.add_parser("status", help="print the state of the system as one JSON object")
    registry = ops_sub.add_parser("event-registry", help="write the event registry JSON")
    registry.add_argument("--out", required=True, help="path of the registry to write")
    rollback = ops_sub.add_parser("can-rollback", help="check a rollback to a previous image")
    rollback.add_argument(
        "--to-registry", required=True, help="event registry of the previous image"
    )
    seed = ops_sub.add_parser("seed-learners", help="create test learners for the load stand")
    seed.add_argument("--count", type=int, required=True, help="number of learners")
    seed.add_argument("--out", required=True, help="path of the learners JSON to write")
    seed.add_argument("--fixture", default="default", help="structure fixture name")
    seed.add_argument("--confirm", action="store_true", help="required when env=prod")
    openapi = ops_sub.add_parser("openapi", help="write the OpenAPI snapshot of the API")
    openapi.add_argument("--out", required=True, help="path of the openapi.json to write")
    return parser


def _run_api(settings: Settings) -> int:
    import uvicorn

    uvicorn.run(
        "quack.api.app:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        workers=settings.api_workers,
        timeout_graceful_shutdown=settings.api_graceful_shutdown_s,
        log_config=None,
    )
    return 0


async def _with_resources[T](
    settings: Settings, fn: Callable[..., Awaitable[T]], *, redis: bool = True
) -> T:
    engine = create_engine(settings)
    driver = create_driver(settings)
    client = create_redis(settings) if redis else None
    try:
        return await fn(engine, create_session_factory(engine), driver, client)
    finally:
        if client is not None:
            await client.aclose()
        await driver.close()
        await engine.dispose()


def _stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    return stop


async def _projector_tasks(settings: Settings, stop: asyncio.Event) -> int:
    """WP8.1: Neo4j schema check, then projector and dispatcher as two supervised tasks
    (WP11.1). On stop each finishes its current batch and starts no new one (Д-§6.1)."""
    context = create_projector_context(settings)
    try:
        missing = await verify_schema(context.neo4j)
        if missing:
            log.error("neo4j_schema_missing", missing=missing)
            return 1
        projector = ProjectorWorker(
            settings, context.engine, context.session_factory, context.neo4j, context.redis
        )
        dispatcher = Dispatcher(settings, context.session_factory, context.redis)
        pause = settings.dispatcher_restart_pause_s
        log.info("projector_started")
        await asyncio.gather(
            supervise("projector", projector.run_forever, stop, restart_pause_s=pause),
            supervise("dispatcher", dispatcher.run_forever, stop, restart_pause_s=pause),
        )
        return 0
    finally:
        await close_projector_context(context)


async def _workers_tasks(settings: Settings, stop: asyncio.Event) -> int:
    """WP8.1: handler registration and completeness check (WP2.2), executors with the reaper
    (graceful shutdown WP3.5) and the periodic scheduler (WP3.3)."""
    handlers = register_all_handlers()
    try:
        check_handlers_complete(handlers)
    except JobHandlersMismatchError as exc:
        log.error("job_handlers_mismatch", missing=exc.missing, extra=exc.extra)
        return 1
    context = await create_job_context(settings)
    try:
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{id(stop)}"
        runner = JobRunner(
            settings, context.session_factory, worker_id, handlers=handlers, context=context
        )
        log.info("workers_started", worker_id=worker_id)
        await asyncio.gather(
            runner.run_forever(stop),
            PeriodicScheduler(settings, context.session_factory).run_forever(stop),
        )
        return 0
    finally:
        await close_job_context(context)


async def _projector(settings: Settings) -> int:
    return await _projector_tasks(settings, _stop_event())


async def _workers(settings: Settings) -> int:
    return await _workers_tasks(settings, _stop_event())


async def _worker(settings: Settings) -> int:
    """Development only: projector and workers in one process (WP8.1)."""
    stop = _stop_event()
    handlers = register_all_handlers()
    try:
        check_handlers_complete(handlers)
    except JobHandlersMismatchError as exc:
        log.error("job_handlers_mismatch", missing=exc.missing, extra=exc.extra)
        return 1
    codes = await asyncio.gather(_projector_tasks(settings, stop), _workers_tasks(settings, stop))
    return max(codes)


async def _rebuild(settings: Settings, args: argparse.Namespace) -> int:
    async def run(engine, session_factory, driver, redis) -> int:
        if args.learner:
            learners = [UUID(args.learner)]
        elif args.stale_rules:
            learners = [
                UUID(x)
                for x in await find_learners_with_rules(driver, current=CURRENT_RULES_VERSION)
            ]
        elif args.all:
            async with session_factory() as session:
                rows = await session.execute(
                    text("SELECT learner_id FROM core.learner_seq ORDER BY learner_id")
                )
                learners = [row[0] for row in rows]
        else:
            log.error("rebuild_target_required")
            return 2
        failed = 0
        for learner_id in learners:
            outcome = await rebuild_learner(
                settings, engine, session_factory, driver, redis, learner_id
            )
            log.info("rebuild_outcome", learner_id=str(learner_id), status=outcome.status)
            failed += outcome.status != "rebuilt"
        return 1 if failed else 0

    return await _with_resources(settings, run)


async def _seed_learners(settings: Settings, args: argparse.Namespace) -> int:
    """WP8.6 (Д-§11): exit code 2 when refused on prod or without JWT_SECRET (the `ops` role
    requires no secrets, Д-§7; tokens signed with an empty key are useless)."""
    if not settings.jwt_secret.get_secret_value():
        log.error("seed_learners_refused", reason="JWT_SECRET is not set; run it in `api`")
        return 2
    engine = create_engine(settings)
    try:
        learners = await seed_learners(
            create_session_factory(engine),
            settings,
            count=args.count,
            fixture=args.fixture,
            confirm=args.confirm,
        )
    except SeedRefusedError as exc:
        log.error("seed_learners_refused", reason=str(exc))
        return 2
    finally:
        await engine.dispose()
    write_learners(Path(args.out), learners)
    log.info("seed_learners_done", count=len(learners), out=args.out)
    return 0


async def _ops(settings: Settings, args: argparse.Namespace) -> int:
    if args.op == "verify-schema":
        driver = create_driver(settings)
        try:
            missing = await verify_schema(driver)
        finally:
            await driver.close()
        if missing:
            log.error("neo4j_schema_missing", missing=missing)
            return 1
        log.info("neo4j_schema_ok")
        return 0
    if args.op == "openapi":
        # Imported here: the function is stream C's (WP8.7) and needs no resources.
        from quack.api.app import write_openapi

        write_openapi(Path(args.out))
        return 0
    if args.op == "rebuild":
        return await _rebuild(settings, args)
    if args.op == "status":

        async def status(engine, session_factory, driver, redis) -> int:
            report, code = await collect_status(settings, session_factory, driver, redis)
            print(json.dumps(report, sort_keys=True))
            return code

        return await _with_resources(settings, status)
    if args.op == "can-rollback":
        previous: dict[str, Any] = args.previous

        async def rollback(engine, session_factory, driver, redis) -> int:
            result = can_rollback(previous, await seen_pairs(session_factory))
            print(json.dumps(result, sort_keys=True))
            return 0 if result["can_rollback"] else 1

        return await _with_resources(settings, rollback, redis=False)
    if args.op == "seed-learners":
        return await _seed_learners(settings, args)
    if args.op == "replay-dlq":
        ids = [UUID(x) for x in args.ids] if args.ids else None

        async def replay(engine, session_factory, driver, redis) -> int:
            await replay_dlq(session_factory, dlq_ids=ids)
            return 0

        return await _with_resources(settings, replay, redis=False)
    if args.op == "purge-deleted":

        async def purge(engine, session_factory, driver, redis) -> int:
            await purge_deleted(
                engine, session_factory, driver, chunk_size=settings.rebuild_max_tx_ops
            )
            return 0

        return await _with_resources(settings, purge, redis=False)
    raise ValueError(f"unknown ops command {args.op!r}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    role = PROCESS_ROLES[args.command]
    # Settings validates the prod secrets of the role (Д-§7, ТЗ WP9.2): set it before reading.
    os.environ["PROCESS_ROLE"] = role
    settings = process_settings(get_settings(), role)
    configure_logging(settings.log_level, settings.secret_values())
    if args.command == "api":
        return _run_api(settings)
    if args.command == "worker":
        if settings.env == "prod":
            log.error("worker_command_is_for_development", hint="run projector and workers")
            return 2
        return asyncio.run(_worker(settings))
    if args.command == "projector":
        return asyncio.run(_projector(settings))
    if args.command == "workers":
        return asyncio.run(_workers(settings))
    if args.op == "migrate":
        migrate(settings)
        return 0
    if args.op == "event-registry":
        write_event_registry(Path(args.out))
        return 0
    if args.op == "can-rollback":
        args.previous = json.loads(Path(args.to_registry).read_text(encoding="utf-8"))
    return asyncio.run(_ops(settings, args))


if __name__ == "__main__":
    sys.exit(main())
