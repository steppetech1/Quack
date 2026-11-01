# Quack backend

Event log, projector, log dispatcher and job queue, LLM gateway on the Responses API, compute
client, FastAPI application. Contracts: `documentation/Спецификация Фаз 1-2 контракты и схемы.md`
and `documentation/ТЗ Фаза 3 (единое).md`. Decisions: [PHASE1_NOTES.md](PHASE1_NOTES.md),
[PHASE2_STREAM2_NOTES.md](PHASE2_STREAM2_NOTES.md), [PHASE3_NOTES.md](PHASE3_NOTES.md); output
contracts of the model roles: [PROMPT_CONTRACTS.md](PROMPT_CONTRACTS.md).

## Checks

```bash
uv sync
uv run ruff check . && uv run ruff format --check . && uv run pyright
QUACK_TEST_REQUIRE_INFRA=1 uv run pytest -q
```

## Processes and CLI

One image (`quack-backend`), one command per process (ТЗ Фазы 3 WP8.1). The CLI sets the
process role before reading `Settings`: in `ENV=prod` each role needs only its own secrets
(`api` — `JWT_SECRET`, `META_API_KEY`, `NEO4J_PASSWORD`; `workers` — `META_API_KEY`,
`NEO4J_PASSWORD`; `projector` — `NEO4J_PASSWORD`; `ops` — none). Every log line carries
`process` and `version` (`QUACK_VERSION`).

```bash
python -m quack.cli api          # HTTP API (Uvicorn, API_WORKERS processes)
python -m quack.cli projector    # projector + log dispatcher (Neo4j schema check first)
python -m quack.cli workers      # job executors, reaper, periodic scheduler
python -m quack.cli worker       # projector + workers in one process, development only

python -m quack.cli ops migrate | verify-schema | status | purge-deleted
python -m quack.cli ops rebuild --learner ID | --stale-rules | --all
python -m quack.cli ops replay-dlq [--id ID ...]
python -m quack.cli ops event-registry --out PATH
python -m quack.cli ops can-rollback --to-registry PATH    # exit 0 — safe, 1 — not safe
python -m quack.cli ops seed-learners --count N --out PATH [--fixture NAME] [--confirm]
python -m quack.cli ops openapi --out openapi.json
```

`ops status` prints one JSON object (dependencies, projector and dispatcher lag, queue, DLQ,
LLM tokens, pressure and open circuits); exit 2 when Postgres does not answer. SIGTERM: `api`
waits `API_GRACEFUL_SHUTDOWN_S`; `workers` takes no new jobs and returns unfinished ones to
`queued` after `WORKER_SHUTDOWN_GRACE_S` without raising `attempt`; `projector` finishes the
current batch.

## Running with compose

`compose.yaml` in the repository root: `postgres`, `neo4j`, `redis`, `migrate`, `api`,
`projector`, `workers`, `compute`. Secrets come from `.env` next to `compose.yaml` (names:
`backend/.env.example` plus `POSTGRES_PASSWORD`). No service publishes a port;
`compose.override.yaml` publishes the databases and the API on 127.0.0.1 for local work.

```bash
docker compose build
docker compose up -d --wait                      # with compose.override.yaml: API on 127.0.0.1:8000
docker compose exec workers python -m quack.cli ops status
```

On a machine with ~4 GB for Docker lower Neo4j memory: `NEO4J_HEAP=384m NEO4J_PAGECACHE=128m
NEO4J_TX_MAX=128m NEO4J_MEM_LIMIT=1g NEO4J_MEMSWAP_LIMIT=1280m`.

## Load stand

Profile `loadtest` adds `meta-mock` (Meta Responses API stand-in, `loadtest/meta_mock.py`,
settings `META_MOCK_*`); point `api` and `workers` to it with
`META_API_BASE_URL=http://meta-mock:8080`.

```bash
docker compose --profile loadtest up -d --wait
docker compose exec api python -m quack.cli ops seed-learners --count 5 --out /tmp/learners.json --confirm
docker compose cp api:/tmp/learners.json learners.json
LOADTEST_LEARNERS=learners.json uv run --group loadtest locust -f loadtest/locustfile.py \
  --host http://127.0.0.1:8000 --headless -u 5 -r 5 -t 2m
```

`seed-learners` runs in `api`, the only service with `JWT_SECRET`; with `ENV=prod` it needs
`--confirm`. Live check of the real Meta API: `scripts/meta_smoke.py` (needs `META_API_KEY`).

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
the `auth`, `core`, `ops` schemas through the Alembic migration. One `pytest` per set of
containers at a time. `tests/e2e/` is the end-to-end stand (`T-E1`…`T-E10`): one application,
projector, dispatcher, executors and scheduler in the test process.

## Layout

| Package | Contents |
| :--- | :--- |
| `quack/events/` | payload models and catalog (`turn.extracted` v2), `payload_hash`, `append_events`, relational projections |
| `quack/graph/` | ids, mutations, Cypher compiler, in-memory and Neo4j stores, Phase A, folds (`rules@2`), evidence weight, topic display, projector, rebuild, slice, overlay |
| `quack/jobs/` | `ops.jobs` protocol, job kinds, runner with graceful shutdown, log dispatcher and its rules, periodic scheduler |
| `quack/runtime/` | process resources (`JobContext`, projector context) and handler registration |
| `quack/llm/`, `quack/prompts/` | gateway (per-model semaphores, retries, pressure flag, circuit breaker, budgets), `ResponsesClient`, strict schemas, labels; prompt registry with `manifest.json` |
| `quack/practice/` | answer check cascade, task selection, `ReferenceSolution`, `task_gen` → bank task |
| `quack/compute_client/` | client of `POST /v1/check/math` |
| `quack/api/` | app factory, resources, JWT, rate limit, errors, OpenAPI, `/health/*`, `/healthz`, `GET /v1/learner/stream` |
| `quack/domains/` | domains, structure, diagnostic, curation, graph view |
| `quack/session/` | sessions, teacher turn (SSE), degradation, spoiler guard, session summary by code, `turn.extract` |
| `quack/artifacts/` | notes, cheatsheets, practice API, search and export |
| `quack/ops/` | `migrate`, `replay-dlq`, `purge-deleted`, `status`, event registry and rollback check, `seed-learners` |
| `quack/db/` | Postgres, Neo4j (`read_session`), Redis keys |
| `loadtest/` | `meta-mock`, Locust scenario, seed fixture |

Adding a prompt: put `quack/prompts/{role}/v{N}.md` and rebuild `quack/prompts/manifest.json`
with `uv run python -m quack.prompts.build_manifest` (never by hand); a released version is
never edited in place.
