"""Infrastructure: migration objects, advisory locks, Neo4j schema, compute client, API
skeleton (ErrorResponse, request ids, health), SSE format, CLI and logging."""

from __future__ import annotations

import json
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from neo4j import AsyncDriver
from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app
from quack.api.errors import ApiError
from quack.api.sse import format_sse
from quack.cli import build_parser
from quack.compute_client.client import (
    ComputeClient,
    ComputeUnavailableError,
    MathCheckRequest,
)
from quack.config import Settings
from quack.db.locks import LockKeyspace, advisory_lock
from quack.db.neo4j import INDEXES, UNIQUE_ID_LABELS, verify_schema
from quack.db.postgres import SessionFactory
from quack.logging import configure_logging, get_logger, log_context


async def test_migration_creates_contract_indexes(
    pg_engine: AsyncEngine, db: SessionFactory
) -> None:
    async with db() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname IN ('core','ops')"
                )
            )
        ).all()
    defs = {name: definition for name, definition in rows}
    assert "WHERE (status = 'running'::text)" in defs["uq_jobs_running_lock"]
    coalesce = defs["uq_jobs_queued_coalesce"]
    assert "WHERE (status = 'queued'::text)" in coalesce and "IS NOT NULL" not in coalesce
    assert "WHERE (status = 'active'::text)" in defs["uq_sessions_one_active"]
    assert "cheatsheet" in defs["uq_artifacts_topic_cheatsheet"]
    assert "to_tsvector('english'" in defs["idx_events_fts_en"]
    assert "UNIQUE" in defs["uq_events_idempotency"]
    assert "(learner_id, session_id, seq)" in defs["idx_events_session_seq"]


async def test_fts_index_query_finds_text_events(db: SessionFactory) -> None:
    from quack.events import append_events
    from tests.factories import new_event, sample_payloads

    learner = uuid4()
    note = dict(sample_payloads()["artifact.note_created"])
    note["text"] = "integration by parts was hard"
    async with db() as session, session.begin():
        await append_events(session, learner, [new_event("artifact.note_created", note)])
    async with db() as session:
        found = (
            await session.execute(
                text(
                    "SELECT count(*) FROM core.events WHERE learner_id = :l AND "
                    "to_tsvector('english', coalesce(payload->>'text', '')) "
                    "@@ plainto_tsquery('english', 'integrate part')"
                ),
                {"l": learner},
            )
        ).scalar_one()
    assert found == 1


async def test_advisory_locks_are_exclusive_per_keyspace(pg_engine: AsyncEngine) -> None:
    learner = uuid4()
    async with advisory_lock(pg_engine, LockKeyspace.LEARNER_PROJECTION, learner) as first:
        assert first is True
        async with advisory_lock(pg_engine, LockKeyspace.LEARNER_PROJECTION, learner) as second:
            assert second is False
        async with advisory_lock(pg_engine, LockKeyspace.SESSION_TURN, learner) as other_space:
            assert other_space is True
        async with advisory_lock(pg_engine, LockKeyspace.LEARNER_PROJECTION, uuid4()) as other:
            assert other is True
    async with advisory_lock(pg_engine, LockKeyspace.LEARNER_PROJECTION, learner) as again:
        assert again is True


async def test_advisory_lock_released_on_error(pg_engine: AsyncEngine) -> None:
    learner = uuid4()
    with pytest.raises(RuntimeError):
        async with advisory_lock(pg_engine, LockKeyspace.LEARNER_PROJECTION, learner):
            raise RuntimeError("boom")
    async with advisory_lock(pg_engine, LockKeyspace.LEARNER_PROJECTION, learner) as acquired:
        assert acquired is True


async def test_neo4j_verify_schema_is_idempotent(neo4j: AsyncDriver) -> None:
    assert await verify_schema(neo4j) == []
    assert await verify_schema(neo4j) == []
    async with neo4j.session() as session:
        result = await session.run("SHOW CONSTRAINTS YIELD name RETURN collect(name) AS n")
        record = await result.single()
    assert record is not None
    assert len([n for n in record["n"] if n.endswith("_id_unique")]) >= len(UNIQUE_ID_LABELS)
    assert len(INDEXES) == 8


# --- compute client --------------------------------------------------------------------


async def test_compute_client_roundtrip() -> None:
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/check/math"
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "is_equivalent": True,
                "normalized_student": "3*x**2",
                "diff_hint": None,
                "error_message": None,
            },
        )

    client = ComputeClient(
        "http://compute:8001", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    response = await client.check_math(
        MathCheckRequest(mode="expression_equiv", student_expr="3x^2", reference_expr="3*x**2")
    )
    assert response.status == "completed" and response.is_equivalent is True
    assert seen[0]["variables"] == ["x"] and seen[0]["timeout_ms"] == 1500
    await client.aclose()


async def test_compute_client_errors() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "x"})

    client = ComputeClient(
        "http://c", client=httpx.AsyncClient(transport=httpx.MockTransport(fail))
    )
    with pytest.raises(ComputeUnavailableError):
        await client.check_math(
            MathCheckRequest(mode="numeric_tol", student_expr="1", reference_expr="1")
        )

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    client = ComputeClient(
        "http://c", client=httpx.AsyncClient(transport=httpx.MockTransport(timeout))
    )
    with pytest.raises(ComputeUnavailableError):
        await client.check_math(
            MathCheckRequest(mode="numeric_tol", student_expr="1", reference_expr="1")
        )
    with pytest.raises(ValidationError):
        MathCheckRequest(mode="numeric_tol", student_expr="1" * 1001, reference_expr="1")


# --- API skeleton ----------------------------------------------------------------------


class Body(BaseModel):
    n: int


def _app_with_test_routes(app: FastAPI) -> FastAPI:
    async def boom() -> None:
        raise ApiError("TURN_LOCKED", "turn in progress", status_code=409, details={"retry": 1})

    async def echo(body: Body) -> dict[str, int]:
        return body.model_dump()

    app.add_api_route("/test/boom", boom, methods=["GET"])
    app.add_api_route("/test/echo", echo, methods=["POST"])
    return app


def test_api_error_format_and_request_id(settings: Settings) -> None:
    app = _app_with_test_routes(create_app(settings, connect=False))
    with TestClient(app) as client:
        response = client.get("/test/boom", headers={"X-Request-ID": "req-123"})
        assert response.status_code == 409
        assert response.headers["X-Request-ID"] == "req-123"
        body = response.json()
        assert body == {
            "error": {
                "code": "TURN_LOCKED",
                "message": "turn in progress",
                "request_id": "req-123",
                "details": {"retry": 1},
            }
        }
        invalid = client.post("/test/echo", json={"n": "x"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
        assert invalid.headers["X-Request-ID"]
        missing = client.get("/nope")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"
        live = client.get("/health/live")
        assert live.status_code == 200 and live.json() == {"status": "ok"}


def test_health_ready_with_real_dependencies(
    settings: Settings, pg_engine: AsyncEngine, neo4j_driver: AsyncDriver
) -> None:
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/health/ready")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "status": "ok",
            "checks": {"postgres": "ok", "neo4j": "ok", "redis": "ok"},
        }


def test_health_ready_reports_failures() -> None:
    broken = Settings(
        env="test",
        postgres_dsn="postgresql://x:y@127.0.0.1:1/none",
        neo4j_uri="bolt://127.0.0.1:1",
        redis_url="redis://127.0.0.1:1/0",
    )
    app = create_app(broken)
    with TestClient(app) as client:
        response = client.get("/health/ready")
        assert response.status_code == 503
        checks = response.json()["checks"]
        assert set(checks) == {"postgres", "neo4j", "redis"}
        assert all(v == "error" for v in checks.values())


def test_sse_format() -> None:
    assert format_sse("turn_started", {"turn_id": "t", "turn_index": 3}) == (
        b'event: turn_started\ndata: {"turn_id":"t","turn_index":3}\n\n'
    )
    assert format_sse("done", {}) == b"event: done\ndata: {}\n\n"


# --- CLI and logging -------------------------------------------------------------------


def test_cli_parser() -> None:
    parser = build_parser()
    assert parser.parse_args(["api"]).command == "api"
    assert parser.parse_args(["worker"]).command == "worker"
    for op in ("migrate", "verify-schema", "replay-dlq", "purge-deleted"):
        args = parser.parse_args(["ops", op])
        assert args.command == "ops" and args.op == op
    learner = str(uuid4())
    args = parser.parse_args(["ops", "rebuild", "--learner", learner])
    assert args.learner == learner and not args.stale_rules
    assert parser.parse_args(["ops", "rebuild", "--stale-rules"]).stale_rules
    assert parser.parse_args(["ops", "rebuild", "--all"]).all
    with pytest.raises(SystemExit):
        parser.parse_args(["ops", "rebuild", "--all", "--stale-rules"])
    with pytest.raises(SystemExit):
        parser.parse_args(["ops", "unknown"])


def test_logging_context_and_secret_masking(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", secret_values=["s3cr3t-value"])
    log = get_logger("test")
    with log_context(request_id="r1", learner_id=uuid4(), seq=5):
        log.info("hello", token="Bearer s3cr3t-value")
    log.info("outside")
    lines = [json.loads(line) for line in capsys.readouterr().out.strip().splitlines()]
    assert lines[0]["request_id"] == "r1" and lines[0]["seq"] == "5"
    assert "s3cr3t-value" not in lines[0]["token"]
    assert "request_id" not in lines[1]
    with pytest.raises(ValueError), log_context(unknown_key=1):
        log.info("unreachable")


def test_settings_require_secrets_in_prod() -> None:
    with pytest.raises(ValidationError):
        Settings(env="prod", jwt_secret="", llm_api_key="")  # type: ignore[arg-type]
    s = Settings(env="test", postgres_dsn="postgresql://a:b@h:5432/d")
    assert s.sqlalchemy_dsn == "postgresql+asyncpg://a:b@h:5432/d"
    assert s.projector_batch_max_events == 100 and s.rebuild_max_tx_ops == 5000
    assert s.llm_semaphore_limit == 32 and s.daily_token_limit_per_learner == 150_000
    assert s.session_token_limit == 40_000 and s.turn_lock_ttl_sec == 60
