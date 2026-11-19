# Quack

Quack is an effective learning tool: it maps a student's knowledge boundary for every
skill — what is proven, failed, or still unknown — and maximizes real understanding per hour
spent rather than following a fixed course syllabus.

Studying is usually poorly organized: materials and walkthroughs vanish across hundreds of AI
chats, mistakes go untracked, review is random or absent, and practice waits until the last
minute. Standard AI chat only makes this worse: it explains smoothly and solves problems for
you — feeling like "I get it" while reading, until the exam proves otherwise; meanwhile, it
forgets what you confused last week and never revisits errors. Organizing this yourself takes
discipline almost no one has. Quack does it for you — without extra discipline or time, just
more real understanding in the same hour.

The user journey is simple: open it, attach a lecture, and learn in your professor's notation
with slide links. The system guides you across 6 mastery levels, tracks misconceptions with
exact quotes, and transfers skills across subjects. Built into the chat are trap questions from
your errors, "why this choice?" prompts, an "unclear" highlight button, and interactive slider
demos. Inside is a two-layer canvas: solve by stylus, keyboard, or on paper via a QR-paired
phone that turns photos into live lines and clean diagrams. It tracks step order, voice, and
crossed-out attempts, pauses for self-correction, and drops a quiet mark or hint right where
you stall. Everything auto-organizes into a subject graph: topics hold living notes with a
"your traps" block, "cue → method" cards, and session links; canvases are searchable by formula
meaning; and deadline hubs bundle mock exams with a cheat sheet. Debt-free FSRS review weaves
into lessons, verdicts are disputable via "disagree," with custom `/skills` and Obsidian/Anki
export.

Behind this stands strong engineering: a false diagnosis is worse than a missed mistake. Models
(Muse Wonder and Spark on Meta Responses API) only propose observations, while deterministic
code computes mastery and guardrails ("honest verdict," no skipping prerequisites, "ask when
uncertain"): formulas are verified via SymPy and pint, and model verdicts require a pair of
distinct tasks. The source of truth is an immutable Postgres event log: a projector builds the
Neo4j graph from it, reducing any dispute, rule change, or deletion to one replay without LLM
calls. The LangGraph teacher turn in the API never waits for background work: it overlays the
session onto an in-memory graph slice and streams the reply (`head → body → markup`), checking
prerequisites before the first word and holding a spoiler buffer. In parallel, a custom Postgres
queue with job coalescing and Redis rate limits drives background agents: SAM 3.1 and OpenCV
parse paper photos, the analyst updates the boundary, second opinion audits uncertain marks, and
the viz agent renders and self-checks demos in headless Chromium.

Deeper design and decision records live in [`documentation/`](documentation/) — start with
`Технический_документ_AI_и_архитектура_памяти_1.md` (architecture) and
`DevOps_развёртывание_и_эксплуатация_1.md` (deployment/ops) if you need more than this README.

## Stack

- **Backend** (`backend/`) — Python 3.14, FastAPI, LangGraph, Postgres (event log) + Neo4j
  (projected graph) + Redis (queues/pub-sub), `uv` for dependency management.
- **Compute** (`compute/`) — a sidecar service for math checking (SymPy/pint), KaTeX/PDF
  rendering, and headless-Chromium visualization.
- **Frontend** (`frontend/`) — Next.js (App Router) + TypeScript + Tailwind.
- **Storage** — any S3-compatible object store for learner objects (strokes, photos, exports);
  the local setup below runs its own MinIO automatically.

## Quick start (local)

Prerequisites: Docker + Docker Compose, Node.js 20+.

1. **Backend + infra, via Docker Compose.** From the repo root:

   ```bash
   cp .env.example .env
   # edit .env: set POSTGRES_PASSWORD / NEO4J_PASSWORD to anything, and META_API_KEY to a real
   # Meta Responses API key (without it the API starts fine, but every teacher turn fails)
   docker compose up -d
   ```

   `compose.override.yaml` is picked up automatically for local runs — it publishes Postgres,
   Neo4j, Redis and the API on `127.0.0.1`, and brings up its own local MinIO (S3-compatible
   object storage, used for canvas strokes/photos/exports) with sane defaults, so you don't need
   real S3 credentials to run locally. Migrations run automatically before the API starts.

   Check everything is up: `docker compose ps` — `api`, `workers`, `projector`, `compute`,
   `minio`, `postgres`, `neo4j`, `redis` should all be `Up` (`api`/`postgres`/`neo4j`/`redis`
   report `healthy`).

2. **Frontend:**

   ```bash
   cd frontend
   npm install
   cp .env.example .env.local
   npm run dev
   ```

   Open `http://localhost:3000`.

3. **Log in.** The real flow is a one-time email link (needs `EMAIL_API_KEY`/a real sender
   configured) or "Continue with Google". For a quick local/demo account without setting up
   email, seed a test learner directly and use its session cookie:

   ```bash
   docker compose exec api python -m quack.cli ops seed-learners --count 1 --out /tmp/learners.json
   docker compose exec api cat /tmp/learners.json
   ```

   The `token` field is the `quack_session` cookie value — set it as a cookie for
   `localhost:3000` (or `localhost:8000` if calling the API directly) in your browser's dev
   tools to sign in as that learner.

## Rebuilding after a backend code change

`backend/quack` is bind-mounted into the `api`/`workers` containers for local development, so
most backend edits are picked up without a rebuild — restart the affected service if you don't
see your change:

```bash
docker compose restart api workers
```

Dependency or Dockerfile changes need a real rebuild:

```bash
docker compose build api workers compute
docker compose up -d
```

## Tests

```bash
cd backend
uv sync
uv run pytest
```

More backend-specific commands (linting, type checks, the CLI, process roles) are in
[`backend/README.md`](backend/README.md).

Integration tests need `dbmate` on `PATH` (pinned version/checksum in `backend/Dockerfile`) and
reachable test instances of Postgres/Neo4j/Redis (`QUACK_TEST_POSTGRES_DSN`,
`QUACK_TEST_NEO4J_URI`, `QUACK_TEST_REDIS_URL` — see `backend/tests/conftest.py`); without
reachable infra they're skipped automatically, and fail instead only with
`QUACK_TEST_REQUIRE_INFRA=1`.

```bash
cd frontend
npm run build   # production build / typecheck
npm run lint
```
