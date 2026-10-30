# AGENT.md

Instructions for AI coding agents (Claude Code, Antigravity, ...) working in this repository.
Read this file first. Before editing files in a folder, also read that folder's AGENT.md
and the AGENT.md of its parent folders (if they exist).

## Project
Quack, an event-sourced AI tutor backend (FastAPI + Postgres event log + Neo4j projection + Redis) in `backend/`.
Details live in docs/CONTEXT.md; backend rules in `backend/CLAUDE.md`.

## How to work here
- Work on ONE task at a time from PLAN.md. Keep commits small and focused.
- Build / run / test commands live in docs/CONTEXT.md. Keep them up to date.
- Outside PLAN.md tasks keep docs/ current (docs/CONTEXT.md, docs/DECISIONS.md). Inside a PLAN.md task report
  decisions in the `summary` of `.nightrun-result.json` instead (see Project-specific rules).
- Stay inside the task scope. Do not reformat or refactor unrelated code.
- Never commit secrets (.env files, keys, tokens).

## Related files
- docs/CONTEXT.md, docs/PROGRESS.md, docs/DECISIONS.md
- docs/agents/*.md (issue tracker, triage labels, domain docs) - only if present
- CLAUDE.md / AGENTS.md - only if present (may hold an "Agent skills" section)

## Project-specific rules
- During a PLAN.md task do not edit docs (`docs/**`, `backend/docs/**`, READMEs, notes files) unless the task's
  `files:` names them: the orchestrator writes the progress log itself, and shared docs cause merge conflicts.
- Stream 3 (`backend/quack/artifacts/**`, `compute/**`, its prompts) is developed in parallel by another team:
  never touch it, and never touch Phase 1 core files.
