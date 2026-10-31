# DECISIONS

Non-obvious decisions and the reasons behind them. Newest first.

Entry format:
## YYYY-MM-DD - <decision>
- Why: <reason>
- Alternatives considered: <short list>

<!-- entries go below this line -->
## 2026-10-30 - Session test fixtures are a pytest plugin (`tests/session/fixtures.py`), not `conftest.py`
- Why: `conftest.py` was rejected as a test-config change. Modules declare `pytest_plugins = ["tests.session.fixtures"]` and import helpers from `tests.session.helpers`.
- Alternatives considered: `tests/session/conftest.py`

## 2026-10-30 - `evaluate_submission` takes `hints_used` and penalties, not `HintView`
- Why: `HintView` has no `independence_penalty` and `quack/graph/overlay.py` is protected. `independence = max(0.1, 1 - sum(penalties))`.
- Alternatives considered: change `HintView`

## 2026-10-30 - `fake_llm`/`fake_compute` are installed both on `app.state` and as `dependency_overrides`
- Why: Works whichever way the handlers obtain the gateway/client; `fake_llm` queues are keyed by response schema name.
- Alternatives considered: one of the two only

## 2026-10-30 - Turn lock: Redis `SET NX EX 60` with Lua compare-and-delete; Postgres advisory lock (keyspace 2) when Redis is None or down
- Why: Per ТЗ §2.3.1 and Т-§17.3; key is deleted only if it still holds our token.
- Alternatives considered: Redis only

## 2026-10-30 - Turn SSE order follows the ТЗ (`task_evaluated` before `task_assigned`), not Спецификация §8.5
- Why: Documents conflict; the ТЗ of stream 2 is the task source. See PHASE2_STREAM2_NOTES.md.
- Alternatives considered: Спецификация order

## 2026-10-30 - Job handlers build their own Redis/`LLMGateway` per job; `run_turn_extract`/`run_session_checkpoint` take them as arguments
- Why: `JobContext` has only settings and session_factory and `quack/jobs/*` is protected. Handlers are registered only when `quack.session.workers`/`lifecycle` are imported.
- Alternatives considered: extend `JobContext`

## 2026-10-30 - Degradation modes: `llm_unavailable` (unavailable/schema error), `budget_exceeded`, `projector_lag` (lag with a working LLM)
- Why: The turn always returns 200 SSE with a task from `core.task_bank` (kinds `math`/`choice`) in the template reply when the LLM fails.
- Alternatives considered: HTTP 5xx

## 2026-10-30 - Re-extraction writes `evidence.superseded(reason="re-extracted")` for the old `turn.extracted` in the same `append_events` call
- Why: Idempotent replacement per law 5; `extractor_done` is published after commit.
- Alternatives considered: separate call

## 2026-10-30 - `session.checkpoint` handler is a no-op when a checkpoint already covers `up_to_turn_index`
- Why: Coalesced jobs may arrive late; avoids a duplicate LLM call and event.
- Alternatives considered: always call

## 2026-10-30 - Events SSE endpoint checks ownership before the response starts and closes pubsub in a shielded `finally`
- Why: Gives a real 404 and no leaked subscriptions on disconnect.
- Alternatives considered: check inside the generator

