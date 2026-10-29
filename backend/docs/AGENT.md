# AGENT.md - docs/

This folder is the project's memory between agent runs. Keep it short, accurate and current.

## Files
- CONTEXT.md   - what the project is, stack, structure, how to build/run/test, conventions, current state.
- PROGRESS.md  - log of finished tasks, newest first.
- DECISIONS.md - non-obvious decisions and their reasons.
- agents/      - (optional) issue tracker / triage labels / domain docs config created by /setup-matt-pocock-skills.

## Update protocol (after EVERY task)
1. Add ONE entry at the top of PROGRESS.md (right below the marker comment): date, task, what changed, notes for the next agent.
2. Update CONTEXT.md only if structure, commands, dependencies or architecture changed. Replace stale text, do not pile up.
3. Add to DECISIONS.md only decisions that a future agent could otherwise undo by mistake.
4. Never paste long logs or code here; link to files instead.
