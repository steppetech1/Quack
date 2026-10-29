# PROGRESS

Newest entries first. Every agent adds ONE short entry at the top after finishing a task.

Entry format:
## YYYY-MM-DD - <task title>
- Changed: <what was done, key files>
- Notes for the next agent: <gotchas, follow-ups, skipped or failing things>

<!-- entries go below this line -->

## 2026-10-30 - T-001 - Stage I skeleton of `quack/session/`: `schemas.py` with request/response models of ТЗ §3 (incl. `TurnView`), `TeacherOutput`/`ConceptExplainedDraft`, `ExtractorOutput` (reusing `ExtractedObservation`/`ExtractedMisconception`/`SkillRef` from `quack/events/payloads.py`), `CheckpointerOutput` exactly as ТЗ §4; prompt templates `quack/prompts/{teacher,extractor,checkpointer}/v1.md` using exactly the `{{ var }}` names of ТЗ §4, with their sha256 under keys `teacher@1`, `extractor@1`, `checkpointer@1` in `manifest.json`; `router.py` with `router = APIRouter(prefix="/v1")` and all 7 endpoints of ТЗ §3 delegating to functions in `lifecycle.py`, `turns.py`, `stream.py`; `deps.py`, `locks.py`, `context.py`, `tasks.py` (task-bank pick and submission evaluation helpers), `workers.py` with typed signatures whose bodies `raise NotImplementedError`; empty `backend/tests/session/__init__.py`. Done when: `cd backend && uv run python -c "from quack.prompts.registry import PromptRegistry as R; r=R(); [r.load(n,1) for n in ('teacher','extractor','checkpointer')]; from quack.session.router import router; assert len(router.routes)==7"` exits 0 and ruff check, ruff format --check, pyright pass
- Changed: Added quack/session skeleton (schemas, router with 7 endpoints, deps/locks/context/tasks/lifecycle/turns/stream/workers stubs raising NotImplementedError), three v1 prompt templates with sha256 in manifest.json, and tests/session/__init__.py. Done-when command, ruff and pyright pass.
- Model: claude (claude-sonnet-5-5)
- Review: skipped (empty review answer)

