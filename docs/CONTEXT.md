# CONTEXT

## Overview
Quack: an AI tutor. This repo holds the backend (`backend/`), a frontend stub (`frontend/`) and the design docs
(`documentation/`). The backend is event-sourced: the Postgres event log (`core.events`) is the source of truth, a
projector builds the learner graph in Neo4j, Redis is used for locks, presence, pub/sub and rate limits.
Detailed backend context: `backend/docs/CONTEXT.md`, rules: `backend/CLAUDE.md`.

## Tech stack
- Python (`.python-version`), package manager `uv` (`backend/uv.lock`).
- FastAPI, SQLAlchemy async + asyncpg, Alembic, neo4j driver, redis-py, pydantic v2, structlog, PyJWT, orjson.
- Dev: pytest + pytest-asyncio (auto mode), ruff, pyright.

## Structure
- `backend/quack/` - Phase 1 core (frozen): `events/`, `graph/`, `jobs/`, `llm/`, `prompts/`, `compute_client/`,
  `api/`, `ops/`, `db/`, `config.py`, `cli.py`. Phase 2: `domains/` (Stream 1), `session/` (Stream 2),
  `artifacts/` (Stream 3, developed in parallel by another team).
- `backend/tests/` - Phase 1 tests; `backend/tests/domains/` (Stream 1, fixtures in its `conftest.py`);
  `backend/tests/session/` (Stream 2, fixtures plugin `tests/session/fixtures.py`, no conftest).
- `backend/migrations/` - Alembic schema (source of truth for tables).
- `documentation/` - tech doc, spec of Phases 1-2, ТЗ of each Phase 2 stream, remediation spec of Streams 1-2.

## Build / run / test (run in `backend/`)
- `uv sync`
- Build: `uv run ruff check && uv run ruff format --check && uv run pyright`
- Tests: `uv run pytest` (integration tests need the containers `quack-test-pg` :55432, `quack-test-neo4j` :57687,
  `quack-test-redis` :56379, see `backend/README.md`; set `QUACK_TEST_REQUIRE_INFRA=1` to fail instead of skip).
- Run: `python -m quack.cli api | worker`

## Conventions
- `backend/CLAUDE.md`: invent nothing beyond the docs/ТЗ, snake_case events `entity.action`, deterministic
  idempotency keys, only the projector writes Neo4j.
- Prompts: `quack/prompts/{role}/v{N}.md` + sha256 in `manifest.json`; never edit a released version.

## Current state
Phase 1 done; Phase 2 Streams 1 and 2 implemented (305 tests green). In progress: remediation of Streams 1-2 by
`documentation/phase2_streams_1_2_remediation_spec.md`, strictly inside `domains/`, `session/` and their tests,
while Stream 3 is developed in parallel.
