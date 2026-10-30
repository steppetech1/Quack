# PROGRESS

Newest entries first. Every agent adds ONE short entry at the top after finishing a task.

Entry format:
## YYYY-MM-DD - <task title>
- Changed: <what was done, key files>
- Notes for the next agent: <gotchas, follow-ups, skipped or failing things>

<!-- entries go below this line -->
## 2026-10-30 - T-015
- Changed: Definition of Done pass: ruff, format, pyright, pytest (305) green; notes in `PHASE2_STREAM2_NOTES.md`, README layout, DECISIONS and PROGRESS.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-014
- Changed: `GET /v1/sessions/{id}/events` Redis pubsub relay as SSE.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-013
- Changed: `session.checkpoint` handler writing `session.checkpointed`.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-012
- Changed: `turn.extract` handler with `evidence.superseded` on re-extraction and `extractor_done` publish.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-011
- Changed: Deterministic LLM degradation (`llm_unavailable`, `budget_exceeded`) with a task from the bank.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-010
- Changed: Projector-lag safety (`projector_lag`) and coalescing `session.checkpoint` enqueue.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-009
- Changed: In-turn task evaluation with SSE `task_evaluated`.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-008
- Changed: Happy-path turn pipeline in `turns.py`: lock, context, teacher@1, events, `turn.extract` job, SSE.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-007
- Changed: `build_turn_context`, `pick_task`, `evaluate_submission` in `context.py`/`tasks.py`.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-006
- Changed: `finish` (idempotent) and `session.sweep_inactive` in `lifecycle.py`.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-005
- Changed: Session start, active, get and heartbeat in `lifecycle.py`; supersedes the old active session.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-004
- Changed: `acquire_turn_lock`: Redis lock with Postgres advisory fallback, `TURN_LOCKED` 409.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-003
- Changed: `deps.py`: JWT auth with tombstone check, turn rate limit (fail-open), redis/gateway/compute dependencies.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-002
- Changed: Shared session test fixtures as a pytest plugin (`tests/session/fixtures.py`) plus `helpers.py`.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

## 2026-10-30 - T-001
- Changed: Stage I skeleton of `quack/session/`: schemas, router with 7 endpoints, stubs, three v1 prompts with manifest hashes.
- Notes for the next agent: see backend/PHASE2_STREAM2_NOTES.md

