# CLAUDE.md — backend

## Role and scope
You work on the Quack **backend only** (`backend/`). The frontend, UI, visual design
and student-facing copy are out of scope (they belong to the product document and mockups).
Do not touch files outside `backend/` unless I explicitly ask.

## Hard rules
1. **Invent nothing.** The only sources of truth are:
   - `documentation/Технический документ AI и архитектура памяти.md` (tech doc)
   - `documentation/DevOps развёртывание и эксплуатация.md` (DevOps doc)
   - the specific task (ТЗ) I give you in the current request.
2. **Stay strictly inside the task.** Do exactly what is asked: no extra features,
   no "while I'm here" refactors, no speculative dependencies or abstractions.
3. **If the docs or the task do not answer a question, stop and ask.**
   Do not fill gaps with "common practice" or assumptions.
4. **If documents conflict, do not choose yourself — report it to me.**
   (Per the docs: a conflict with the product document means the technical decision
   is wrong; tech-doc core laws and contracts outrank the DevOps doc.)

## How to read decisions in the tech doc (§0.5)
- **Contract** — never violate. Changing it requires a separate team decision
  and a doc update.
- **Implementation guideline** — may be changed if the contract is kept.
- **Verify ("Проверить")** — do not rely on it in code until verified.
  Report it to me instead of implementing it.
- Status labels: ПРИНЯТО — implement. НА ОБСУЖДЕНИИ — ask me first.
  ПОЗЖЕ — do not implement.
- The hackathon slice (§22) takes precedence where it differs from the target picture.

## Core laws (§1.1) — no component may violate them
1. Source of truth is the event log in Postgres. Everything affecting the student
   model is written as an event first. Neo4j can be dropped and rebuilt.
2. The model interprets, code computes. Models only produce structured observations;
   levels, retention and statuses are computed by deterministic code.
   Rebuild never calls models.
3. One writer per store. Only the projector writes to Neo4j. Teacher, agents and
   API write events.
4. Evidence strength is assigned by code (source type and verdict check class),
   never by model opinion. A single model judgment cannot worsen a student's state.
5. Idempotency via deterministic keys (event number, source + extractor version,
   task id). A retry replaces the record, never duplicates it.
6. Provenance: every evidence item, misconception and topic state must trace back
   to raw material (messages, canvas lines, task attempts).
7. A failed background step commits nothing. Background work is applied fully or
   not at all, and never affects the teacher turn.
8. Absence is not zero. "Not checked" is an explicit value, distinct from "failed".
   In Neo4j, assigning null deletes the property, so use `'unknown'`.
9. The hot path never waits for background processes (see §2.6 for overlaying
   unapplied current-session events).
10. No accumulation of conclusions. Models do not build memory on their own past
    outputs; teacher summaries are rebuilt from structure each time.

## Code conventions (§0.4)
- Identifiers in English. `snake_case` for Python and Postgres, `camelCase` for
  Neo4j properties, `PascalCase` for Neo4j labels.
- Event types: `entity.action` in past tense (`answer.submitted`, `turn.committed`,
  `observation.extracted`, `topic.merged`).
- Model roles are nouns: `teacher`, `extractor`, `diagnoser`, `analyst`.
- Prompt and rule versions: `name@N` (`extractor@3`, `rules@7`).
- Thresholds and numbers go to config under a named parameter; never hardcode them.
  Values from the docs are starting values, not truth.
- Full column/index details live in migrations, which are the source of truth for schemas.

## Environment
- Python 3.14, package manager `uv` (`uv sync`, `uv add`, `uv run`).
- Add only dependencies named in the docs or the task.
- Do not use external-service features or limits marked "verify" until verified.

## Git
- Commit only as me. No `Co-authored-by` lines and no mention of AI in commits.
- Do not commit or push unless explicitly asked. Never force-push.
- Never commit secrets or `.env` (see DevOps §5.3 and §7).

## Working style
- Before starting, briefly name the doc sections you rely on.
- If a request goes outside the backend or the task, say so and do not do it.
- Cite sections as in the docs: `§2.3` (tech doc), `Д-§8.1` (DevOps doc).
- Reply concisely, in Russian.
