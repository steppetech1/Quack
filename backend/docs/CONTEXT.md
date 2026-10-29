# CONTEXT

## Overview
Quack: an AI tutor. This repo holds the backend (`backend/`), a frontend stub (`frontend/`) and the design docs
(`documentation/`). The backend is event-sourced: the Postgres event log is the source of truth, a projector builds
the learner graph in Neo4j, Redis is used for locks, presence, pub/sub and rate limits.

## Tech stack
- Python 3.12+ (`.python-version`), package manager `uv` (`backend/uv.lock`).
- FastAPI, SQLAlchemy async + asyncpg, Alembic, neo4j driver, redis-py, pydantic v2, structlog, PyJWT.
- Dev: pytest + pytest-asyncio (auto mode), ruff, pyright (standard mode).

## Structure
- `backend/quack/` - packages: `events/`, `graph/`, `jobs/`, `llm/`, `prompts/`, `compute_client/`, `api/`, `ops/`, `db/` (Phase 1 core, frozen);
  `session/` (Phase 2 Stream 2), `domains/` (Stream 1), `artifacts/` (Stream 3).
- `backend/tests/` - Phase 1 tests; `backend/tests/session/` - Stream 2 tests.
- `backend/migrations/` - Alembic schema (source of truth for tables).
- `documentation/` - tech doc, spec of Phases 1-2, ТЗ of each Phase 2 stream.
- `backend/PHASE1_NOTES.md` - Phase 1 decisions and open questions.

## Build / run / test (run in `backend/`)
- `uv sync`
- Lint/types: `uv run ruff check && uv run ruff format --check && uv run pyright`
- Tests: `uv run pytest` (integration tests need the containers from `backend/README.md`; set `QUACK_TEST_REQUIRE_INFRA=1` to fail instead of skip)
- Run: `python -m quack.cli api | worker`

## Conventions
- See `backend/CLAUDE.md`: invent nothing beyond the docs/ТЗ, thresholds live in `Settings`, snake_case events `entity.action`.
- Prompts: `quack/prompts/{role}/v{N}.md` + sha256 in `manifest.json`; never edit a released version.

## Current state
Phase 1 done. Phase 2 Stream 2 (sessions/teacher/extractor/degradation) is planned in PLAN.md.
