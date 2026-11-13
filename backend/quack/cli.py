"""Entry point: python -m quack.cli api | worker | ops <command> (Д-§5.1, Д-§6)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import structlog

import quack.db
from quack.agents.extraction_eval import compare_reports, evaluate_extraction, load_eval_set
from quack.agents.judge import release_judge
from quack.agents.metrics import metrics
from quack.config import Settings, get_settings
from quack.db import open_db
from quack.db.neo4j import create_driver, verify_schema
from quack.db.redis import create_redis
from quack.deletion.journal import replay_deletions
from quack.graph.projection.rebuild import rebuild_learner
from quack.graph.projection.runner import RULES_VERSION
from quack.graph.projection.runner import resume as resume_stream
from quack.graph.projection.store import stale_learners
from quack.graph.read.reader import create_graph_reader
from quack.llm.gateway import LLMGateway
from quack.llm.responses_client import ResponsesClient
from quack.logging import configure_logging, get_logger
from quack.ops.alerts import run_alerts
from quack.ops.backup import run_backup
from quack.ops.commands import AlembicSchemaFound, migrate
from quack.ops.db_roles import DbRolesRefused, RolePasswords, apply_db_roles
from quack.ops.orphan_objects import orphan_objects
from quack.ops.restore_check import restore_check
from quack.ops.seed import SeedRefusedError, seed_learners, write_learners
from quack.ops.status import collect_status
from quack.prompts.registry import PromptNotFoundError
from quack.runtime import (
    JobHandlersMismatchError,
    check_handlers_complete,
    close_job_context,
    create_job_context,
    register_all_handlers,
)
from quack.runtime.event_registry import can_rollback, seen_pairs, write_event_registry
from quack.runtime.phase4 import projector_process, workers_process
from quack.session.replay import replay_session, session_learner
from quack.storage.deletions import create_deletion_journal
from quack.storage.objects import create_object_store

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


def _since(value: str) -> datetime:
    """`--since`: ISO 8601; a time without a zone is UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not an ISO 8601 time: {value!r}") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        parsed = 0
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"not a positive integer: {value!r}")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m quack.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("api", help="run the HTTP API (uvicorn)")
    sub.add_parser("projector", help="run the projector and the log dispatcher")
    sub.add_parser("workers", help="run the job executors and the periodic scheduler")
    sub.add_parser("worker", help="projector + workers in one process (development only)")
    ops = sub.add_parser("ops", help="operational commands")
    ops_sub = ops.add_subparsers(dest="op", required=True)
    ops_sub.add_parser("migrate", help="dbmate up on backend/db/migrations")
    ops_sub.add_parser("verify-schema", help="create/verify Neo4j constraints and indexes")
    rebuild = ops_sub.add_parser("rebuild", help="rebuild learner projections from the log")
    target = rebuild.add_mutually_exclusive_group(required=False)
    target.add_argument("--learner", help="learner id")
    target.add_argument("--stale-rules", action="store_true", help="learners on other rules@N")
    target.add_argument("--all", action="store_true", help="every learner")
    rebuild.add_argument(
        "--concurrency",
        type=_positive_int,
        help="learners rebuilt at the same time (default: rebuild_concurrency)",
    )
    backup = ops_sub.add_parser("backup", help="encrypted Postgres dump to R2 and local copies")
    backup.add_argument("when", nargs="?", choices=["now"], help="an out-of-schedule dump")
    ops_sub.add_parser(
        "restore-check", help="Neo4j must not be ahead of the event log after a restore"
    )
    alerts_p = ops_sub.add_parser("alerts", help="check the conditions of Д-§9.3 and announce them")
    alerts_p.add_argument("--test", action="store_true", help="send a test alert message")
    ops_sub.add_parser("db-roles", help="create or update the Postgres roles and grants")
    resume = ops_sub.add_parser("resume", help="release the stopped projector stream of a learner")
    resume.add_argument("--learner", type=UUID, required=True, help="learner id")
    replay_d = ops_sub.add_parser(
        "replay-deletions", help="run the deletion journal again after a restore (Д-§8.3)"
    )
    replay_d.add_argument(
        "--since", type=_since, required=True, help="ISO 8601 time of the dump; no zone means UTC"
    )
    orphans = ops_sub.add_parser(
        "orphan-objects",
        help="list learner files newer than a time without a record in Postgres "
        "(dry run; --apply deletes them)",
    )
    orphans.add_argument(
        "--since", type=_since, required=True, help="ISO 8601 time of the dump; no zone means UTC"
    )
    orphans.add_argument(
        "--apply", action="store_true", help="delete the listed files (without it: only report)"
    )
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
    extraction = ops_sub.add_parser(
        "extraction-eval", help="quality of the extraction on a reference set (Т-§7.11)"
    )
    extraction.add_argument("--set", required=True, help="reference set (EvalSet JSON)")
    extraction.add_argument(
        "--learner", type=UUID, required=True, help="evaluation learner: whose ledger is charged"
    )
    extraction.add_argument("--prompt", help="extractor prompt, name@N (default: the role's)")
    extraction.add_argument("--runs", type=int, help="runs per episode (default: from Settings)")
    extraction.add_argument("--out", help="path of the report JSON to write")
    extraction.add_argument(
        "--against", help="report of the previous version: compare, exit 1 unless nothing fell"
    )
    ops_metrics = ops_sub.add_parser(
        "metrics", help="metrics of Т-§18.2 and Т-§18.3 over the events from a time, as JSON"
    )
    ops_metrics.add_argument(
        "--since", type=_since, required=True, help="ISO 8601 time; no zone means UTC"
    )
    replay_s = ops_sub.add_parser(
        "replay-session", help="replay the teacher turns of a session with another prompt"
    )
    replay_s.add_argument("--session", type=UUID, required=True, help="session id")
    replay_s.add_argument("--teacher", required=True, help="prompt of the teacher role, name@N")
    replay_s.add_argument("--renderer", type=int, help="renderer version N (renderer@N)")
    replay_s.add_argument(
        "--out", help="report path (default: replay-session-<session id>.json in the cwd)"
    )
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
        proxy_headers=True,
        forwarded_allow_ips=settings.api_forwarded_allow_ips,
        log_config=None,
    )
    return 0


@asynccontextmanager
async def _gateway(settings: Settings, db: quack.db.Db) -> AsyncIterator[LLMGateway]:
    """The model gateway of `ops extraction-eval` and `ops replay-session`: on `Db` only
    (D-B-212), so the CLI builds no SQLAlchemy engine."""
    provider = ResponsesClient(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    llm = LLMGateway(provider, settings, db=db)
    try:
        yield llm
    finally:
        await llm.aclose()


def _stop_event() -> asyncio.Event:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    return stop


async def _projector_tasks(settings: Settings, stop: asyncio.Event) -> int:
    """WP8.1: the projector (`rules@3`) and the dispatcher as two supervised tasks on `Db`
    (WP1.3, D-A-200). On stop each finishes its current batch and starts no new one (Д-§6.1)."""
    return await projector_process(settings, stop)


async def _workers_tasks(settings: Settings, stop: asyncio.Event) -> int:
    """WP8.1: handler registration and completeness check (WP2.2), the executors with the reaper
    (graceful shutdown WP3.5) and the periodic scheduler (WP3.3), on `Db` (D-A-200)."""
    handlers = register_all_handlers()
    try:
        check_handlers_complete(handlers)
    except JobHandlersMismatchError as exc:
        log.error("job_handlers_mismatch", missing=exc.missing, extra=exc.extra)
        return 1
    context = await create_job_context(settings)
    try:
        return await workers_process(settings, stop, handlers=handlers, ctx=context)
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


async def _rebuild_each(
    learners: list[UUID], rebuild: Callable[[UUID], Awaitable[Any]], concurrency: int = 1
) -> int:
    """Every learner is tried, `concurrency` at a time; anything but `rebuilt` (busy, active
    session, failed) is exit 1."""
    gate = asyncio.Semaphore(concurrency)

    async def one(learner_id: UUID) -> int:
        async with gate:
            outcome = await rebuild(learner_id)
        log.info("rebuild_outcome", learner_id=str(learner_id), status=outcome.status)
        return int(outcome.status != "rebuilt")

    failed = sum(await asyncio.gather(*(one(learner_id) for learner_id in learners)))
    return 1 if failed else 0


async def _rebuild(settings: Settings, args: argparse.Namespace) -> int:
    """WP3.5, D-A-189: rules@3 on `Db`; `--stale-rules` is the learners of other rules."""
    if not (args.learner or args.stale_rules or args.all):
        log.error("rebuild_target_required")
        return 2
    db = await open_db(settings)
    neo4j = create_driver(settings)
    redis = create_redis(settings)
    try:
        if args.learner:
            learners = [UUID(args.learner)]
        elif args.stale_rules:
            learners = [UUID(x) for x in await stale_learners(neo4j, RULES_VERSION)]
        else:
            rows = await db.fetch_all("SELECT learner_id FROM core.learners ORDER BY learner_id")
            learners = [UUID(str(row["learner_id"])) for row in rows]
        return await _rebuild_each(
            learners,
            lambda lid: rebuild_learner(db, neo4j, settings, lid, redis=redis),
            args.concurrency or settings.rebuild_concurrency,
        )
    finally:
        await redis.aclose()
        await neo4j.close()
        await db.close()


async def _seed_learners(settings: Settings, args: argparse.Namespace) -> int:
    """WP8.6 (Д-§11): exit code 2 when refused on prod. Tokens are sessions in
    `core.auth_sessions` (ТЗ Ф4 WP18.3), so the `ops` role needs no secret (Д-§7)."""
    db = await open_db(settings)
    try:
        learners = await seed_learners(
            db, settings, count=args.count, fixture=args.fixture, confirm=args.confirm
        )
    except SeedRefusedError as exc:
        log.error("seed_learners_refused", reason=str(exc))
        return 2
    finally:
        await db.close()
    write_learners(Path(args.out), learners)
    log.info("seed_learners_done", count=len(learners), out=args.out)
    return 0


async def _metrics(settings: Settings, args: argparse.Namespace) -> int:
    """WP16.7: the metrics of the window as one JSON object on stdout. The reader of the Brier
    score is bound to the database (`state_at` replays the log)."""
    db = await open_db(settings)
    driver = create_driver(settings)
    try:
        report = await metrics(
            db,
            args.since,
            settings=settings,
            reader=lambda learner_id: create_graph_reader(
                driver, settings, learner_id=learner_id, db=db
            ),
        )
    finally:
        await driver.close()
        await db.close()
    print(json.dumps(report, sort_keys=True, default=str))
    return 0


async def _replay_session(settings: Settings, args: argparse.Namespace) -> int:
    """WP16.7: exit code 2 when the session is unknown, the prompt is not registered or there is
    no model key (the replay calls the model; the `ops` role has none in prod, so run it where
    `META_API_KEY` is set). The report goes to `--out`, its summary to stdout."""
    if not settings.meta_api_key.get_secret_value():
        log.error("replay_session_needs_meta_api_key")
        return 2
    out = Path(args.out) if args.out else Path(f"replay-session-{args.session}.json")
    db = await open_db(settings)
    driver = create_driver(settings)
    try:
        learner_id = await session_learner(db, args.session)
        if learner_id is None:
            log.error("replay_session_unknown_session", session_id=str(args.session))
            return 2
        try:
            async with _gateway(settings, db) as llm:
                report = await replay_session(
                    db,
                    learner_id=learner_id,
                    session_id=args.session,
                    teacher=args.teacher,
                    llm=llm,
                    settings=settings,
                    reader=lambda lid: create_graph_reader(driver, settings, learner_id=lid, db=db),
                    judge=release_judge(llm),
                    out=out,
                    renderer=args.renderer,
                )
        except (PromptNotFoundError, ValueError) as exc:
            log.error("replay_session_refused", reason=str(exc))
            return 2
    finally:
        await driver.close()
        await db.close()
    print(json.dumps({"out": str(out), "summary": report["summary"]}, sort_keys=True, default=str))
    return 0


def _read_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


async def _extraction_eval(settings: Settings, args: argparse.Namespace) -> int:
    """WP16.7, S15 (D-B-209): the report of the extraction quality as one JSON object on stdout.
    Exit code 2 when there is no model key (the run calls the model), the set or the previous
    report cannot be read, or the prompt or the run count is refused. With `--against`, exit
    code 1 unless the comparison of Т-§18.4 found no fall (not comparable is not «passed»)."""
    if not settings.meta_api_key.get_secret_value():
        log.error("extraction_eval_needs_meta_api_key")
        return 2
    try:
        eval_set = load_eval_set(Path(args.set))
        previous = _read_report(Path(args.against)) if args.against else None
    except (OSError, ValueError) as exc:  # unreadable, not JSON, not an EvalSet
        log.error("extraction_eval_input_refused", reason=str(exc))
        return 2
    db = await open_db(settings)
    try:
        async with _gateway(settings, db) as llm:
            report = await evaluate_extraction(
                llm,
                settings,
                eval_set,
                learner_id=args.learner,
                prompt=args.prompt,
                runs=args.runs,
                out=Path(args.out) if args.out else None,
            )
    except ValueError as exc:
        log.error("extraction_eval_refused", reason=str(exc))
        return 2
    finally:
        await db.close()
    code = 0
    if previous is not None:
        report = {**report, "comparison": compare_reports(previous, report)}
        code = 0 if report["comparison"]["regression"] is False else 1
    print(json.dumps(report, sort_keys=True, default=str))
    return code


async def _restore_check(settings: Settings) -> int:
    """Д-§8.3: prints the verdict as one JSON object; exit code 1 when Neo4j is ahead of the log."""
    db = await open_db(settings)
    driver = create_driver(settings)
    try:
        violations = await restore_check(db, driver)
    finally:
        await driver.close()
        await db.close()
    report = {
        "ok": not violations,
        "violations": [
            {"learner_id": v.learner_id, "applied_seq": v.applied_seq, "log_seq": v.log_seq}
            for v in violations
        ],
    }
    print(json.dumps(report, sort_keys=True))
    return 1 if violations else 0


async def _resume(settings: Settings, args: argparse.Namespace) -> int:
    """WP9.6, Д-§8.7: the projector goes on with the learner from the failed event."""
    db = await open_db(settings, wait=False)
    try:
        await resume_stream(db, args.learner)
    finally:
        await db.close()
    log.info("projector_stream_resumed", learner_id=str(args.learner))
    return 0


async def _replay_deletions(settings: Settings, args: argparse.Namespace) -> int:
    """WP9.6, Д-§8.3 step 5: every record of `quack-deletions` since the dump runs again."""
    db = await open_db(settings)
    neo4j = create_driver(settings)
    redis = create_redis(settings)
    store = create_object_store(settings)
    journal = create_deletion_journal(settings)
    try:
        done = await replay_deletions(db, neo4j, settings, journal, store, args.since, redis=redis)
    finally:
        await journal.aclose()
        await store.aclose()
        await redis.aclose()
        await neo4j.close()
        await db.close()
    print(json.dumps({"replayed": done}, sort_keys=True))
    return 0


async def _orphan_objects(settings: Settings, args: argparse.Namespace) -> int:
    """WP9.6, Д-§8.3 step 8: files of learners newer than the dump with no record are listed;
    they are deleted only with `--apply` (D-C-215)."""
    db = await open_db(settings)
    store = create_object_store(settings)
    try:
        report = await orphan_objects(db, store, args.since, apply=args.apply)
    finally:
        await store.aclose()
        await db.close()
    print(
        json.dumps(
            {
                "checked": report.checked,
                "orphans": report.orphans,
                "deleted": report.deleted,
                "dry_run": report.dry_run,
            },
            sort_keys=True,
        )
    )
    return 0


async def _status(settings: Settings) -> int:
    """Д-§9.1: the report on stdout; exit code 2 when Postgres does not answer. The pool opens
    without waiting, so a dead Postgres is a report with code 2, not a traceback."""
    db = await open_db(settings, wait=False)
    driver = create_driver(settings)
    redis = create_redis(settings)
    try:
        report, code = await collect_status(settings, db, driver, redis)
    finally:
        await redis.aclose()
        await driver.close()
        await db.close()
    print(json.dumps(report, sort_keys=True))
    return code


async def _alerts(settings: Settings, *, test: bool = False) -> int:
    """Д-§9.3: Postgres and Redis only (the `alerts` container has no Neo4j secret, Д-§7)."""
    if test:
        from quack.ops.notify import post_alert

        try:
            await post_alert(
                settings, "INFO: Test alert from Quack ops alerts — Telegram alerts work!"
            )
            print("Test alert sent successfully.")
            return 0
        except Exception as exc:
            print(f"Failed to send test alert: {exc}", file=sys.stderr)
            return 1
    db = await open_db(settings, wait=False)
    redis = create_redis(settings)
    try:
        status, _ = await collect_status(settings, db, None, redis)
        return await run_alerts(settings, status, redis)
    finally:
        await redis.aclose()
        await db.close()


async def _db_roles(settings: Settings) -> int:
    """Д-§7: needs the superuser DSN and the three role passwords; exit code 2 when refused."""
    try:
        passwords = RolePasswords.from_settings(settings)
    except DbRolesRefused as exc:
        log.error("db_roles_refused", reason=str(exc))
        return 2
    db = await open_db(settings)
    try:
        await apply_db_roles(db, passwords)
    finally:
        await db.close()
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
    if args.op == "backup":
        return await run_backup(settings)
    if args.op == "db-roles":
        return await _db_roles(settings)
    if args.op == "restore-check":
        return await _restore_check(settings)
    if args.op == "resume":
        return await _resume(settings, args)
    if args.op == "replay-deletions":
        return await _replay_deletions(settings, args)
    if args.op == "orphan-objects":
        return await _orphan_objects(settings, args)
    if args.op == "alerts":
        return await _alerts(settings, test=getattr(args, "test", False))
    if args.op == "openapi":
        # Imported here: the function is stream C's (WP8.7) and needs no resources.
        from quack.api.app import write_openapi

        write_openapi(Path(args.out))
        return 0
    if args.op == "metrics":
        return await _metrics(settings, args)
    if args.op == "replay-session":
        return await _replay_session(settings, args)
    if args.op == "rebuild":
        return await _rebuild(settings, args)
    if args.op == "extraction-eval":
        return await _extraction_eval(settings, args)
    if args.op == "status":
        return await _status(settings)
    if args.op == "can-rollback":
        previous: dict[str, Any] = args.previous

        db = await open_db(settings)
        try:
            result = can_rollback(previous, await seen_pairs(db))
        finally:
            await db.close()
        print(json.dumps(result, sort_keys=True))
        return 0 if result["can_rollback"] else 1
    if args.op == "seed-learners":
        return await _seed_learners(settings, args)
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
        try:
            migrate(settings)
        except (AlembicSchemaFound, subprocess.CalledProcessError, FileNotFoundError) as exc:
            log.error("ops_migrate_failed", error=str(exc))
            return 1
        return 0
    if args.op == "event-registry":
        write_event_registry(Path(args.out))
        return 0
    if args.op == "can-rollback":
        args.previous = json.loads(Path(args.to_registry).read_text(encoding="utf-8"))
    return asyncio.run(_ops(settings, args))


if __name__ == "__main__":
    sys.exit(main())
