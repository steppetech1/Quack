"""Entry point: python -m quack.cli api | worker | ops <command> (Д-§5.1, Д-§6)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import socket
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import UUID

from sqlalchemy import text

from quack.config import Settings, get_settings
from quack.db.neo4j import create_driver, verify_schema
from quack.db.postgres import create_engine, create_session_factory
from quack.db.redis import create_redis
from quack.graph.ids import CURRENT_RULES_VERSION
from quack.graph.neo4j_store import find_learners_with_rules
from quack.graph.rebuild import rebuild_learner
from quack.graph.worker import ProjectorWorker
from quack.jobs.periodic import PeriodicScheduler
from quack.jobs.runner import JobRunner
from quack.logging import configure_logging, get_logger
from quack.ops.commands import migrate, purge_deleted, replay_dlq
from quack.ops.seed import SeedRefusedError, seed_learners, write_learners
from quack.runtime import (
    JobHandlersMismatchError,
    check_handlers_complete,
    close_job_context,
    create_job_context,
    register_all_handlers,
)

log = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m quack.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api", help="run the HTTP API (uvicorn)")
    sub.add_parser("worker", help="run the projector and the job executors")
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


async def _worker(settings: Settings) -> int:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)

    handlers = register_all_handlers()
    try:
        check_handlers_complete(handlers)
    except JobHandlersMismatchError as exc:
        log.error("job_handlers_mismatch", missing=exc.missing, extra=exc.extra)
        return 1

    context = await create_job_context(settings)
    try:
        engine, driver = context.engine, context.neo4j
        missing = await verify_schema(driver)
        if missing:
            log.error("neo4j_schema_missing", missing=missing)
            return 1
        worker_id = f"{socket.gethostname()}:{id(stop)}"
        projector = ProjectorWorker(
            settings, engine, context.session_factory, driver, context.redis
        )
        runner = JobRunner(
            settings,
            context.session_factory,
            worker_id,
            handlers=handlers,
            context=context,
        )
        log.info("worker_started", worker_id=worker_id)
        await asyncio.gather(
            projector.run_forever(stop),
            runner.run_forever(stop),
            PeriodicScheduler(settings, context.session_factory).run_forever(stop),
        )
        return 0
    finally:
        await close_job_context(context)


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
    """WP8.6 (Д-§11): exit code 2 when refused on prod."""
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
    settings = get_settings()
    configure_logging(settings.log_level, settings.secret_values())
    if args.command == "api":
        return _run_api(settings)
    if args.command == "worker":
        return asyncio.run(_worker(settings))
    if args.op == "migrate":
        migrate(settings)
        return 0
    return asyncio.run(_ops(settings, args))


if __name__ == "__main__":
    sys.exit(main())
