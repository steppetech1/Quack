# Quack backend

Phase 1 core: event log, projector (Phases A/B/C), consistent reads and session overlay,
job queue, LLM gateway, compute client, FastAPI skeleton. Contracts:
`documentation/Спецификация Фаз 1-2 контракты и схемы.md`. Decisions and open questions of
Phase 1: [PHASE1_NOTES.md](PHASE1_NOTES.md).

## Commands

```bash
uv sync
uv run ruff check && uv run ruff format --check && uv run pyright && uv run pytest

python -m quack.cli api | worker
python -m quack.cli ops migrate | verify-schema | replay-dlq [--id ID] | purge-deleted
python -m quack.cli ops rebuild --learner ID | --stale-rules | --all
```

## Test infrastructure

Integration tests use real Postgres 16, Neo4j and Redis. Without them those tests are
skipped (set `QUACK_TEST_REQUIRE_INFRA=1` to make them fail instead).

```bash
docker run -d --name quack-test-pg -e POSTGRES_USER=quack -e POSTGRES_PASSWORD=quack \
  -e POSTGRES_DB=quack_test -p 55432:5432 postgres:16
docker run -d --name quack-test-neo4j -e NEO4J_AUTH=neo4j/quacktest123 -p 57687:7687 \
  neo4j:2026.06-community
docker run -d --name quack-test-redis -p 56379:6379 redis:7
```

Override with `QUACK_TEST_POSTGRES_DSN`, `QUACK_TEST_NEO4J_URI`, `QUACK_TEST_NEO4J_USER`,
`QUACK_TEST_NEO4J_PASSWORD`, `QUACK_TEST_REDIS_URL`. The test session drops and re-creates
the `auth`, `core`, `ops` schemas through the Alembic migration.

## Layout

| Package | Contents |
| :--- | :--- |
| `quack/events/` | 36 payload models, registry, `payload_hash`, `append_events`, relational projections |
| `quack/graph/` | ids, mutations, Cypher compiler, in-memory and Neo4j stores, Phase A, folds, topic display, projector, worker, rebuild, slice, overlay |
| `quack/jobs/` | `ops.jobs` protocol, job kinds, runner (`register_job_handler`) |
| `quack/llm/`, `quack/prompts/` | gateway with outbox/ledger/budgets, provider, prompt registry with `manifest.json` |
| `quack/compute_client/` | client of `POST /v1/check/math` |
| `quack/api/` | app factory, ErrorResponse, request/trace ids, SSE helpers, `/health/*` |
| `quack/ops/` | `migrate`, `replay-dlq`, `purge-deleted` |
| `quack/domains/`, `quack/session/`, `quack/artifacts/` | empty, Phase 2 streams |

Adding a prompt: put `quack/prompts/{role}/v{N}.md` and add `"{role}@{N}": "<sha256>"` to
`quack/prompts/manifest.json`; a released version is never edited in place.
