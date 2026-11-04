"""T-OPS8 (ТЗ Ф4 §8.13, WP9.4): the steps of `deploy/deploy.sh` follow Д-§6.1, and a release that
changed events, rules or the schema is never rolled back automatically (Д-§6.3).

The script talks to Docker and a server, so the order is read from the script itself: the
numbered step comments, and the commands each step is made of (D-C-210)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "deploy.sh"
TEXT = SCRIPT.read_text(encoding="utf-8")
BASH = shutil.which("bash")


def position(needle: str, start: int = 0) -> int:
    found = TEXT.find(needle, start)
    assert found >= 0, f"deploy.sh has no {needle!r}"
    return found


@pytest.mark.skipif(BASH is None, reason="bash is not available")
def test_the_script_parses() -> None:
    assert BASH is not None
    done = subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_steps_come_in_the_order_of_the_document() -> None:
    """Д-§6.1 steps 1-8: version, registries, dump, migrate, projector, compute and workers,
    api, check."""
    marks = [
        "# 1. Version",
        "# 2. New events",
        "# 3. Dump",
        "# 4. Migrations",
        "# 5-7.",
        "# 8. Check",
    ]
    order = [position(mark) for mark in marks]
    assert order == sorted(order)
    assert order[0] < position("docker pull") < order[1]
    assert order[1] < position("compare_registries", order[1]) < order[2]
    dump = position("ops backup now")
    migrate = position("dc run --rm migrate;", dump)
    assert order[2] < dump < order[3] < migrate < order[4]
    # In `rollout`: databases, projector, compute and workers, api, then Caddy.
    body = TEXT[position("rollout() {") : position("PREVIOUS=")]
    parts = [
        "postgres neo4j redis",
        "--no-deps projector",
        "--no-deps compute workers",
        "--no-deps api",
        "--no-deps caddy",
    ]
    where = [body.find(part) for part in parts]
    assert -1 not in where
    assert where == sorted(where)
    assert position("\nrollout\n") < position("if check_release; then")


def test_the_roles_come_before_the_dump_and_the_migration() -> None:
    """Д-§7: `ops db-roles` first (the dump reads as `quack_reader`)."""
    assert position("ops db-roles") < position("ops backup now") < position("dc run --rm migrate;")


def test_a_release_with_new_events_or_schema_is_not_rolled_back_automatically() -> None:
    """Д-§6.3: the automatic return needs a previous release and no new events, no new rules,
    no new migrations; otherwise the script stops and points at `ops can-rollback`."""
    failed = TEXT[position('log "check failed') :]
    gate = failed[failed.index("if [[") : failed.index("then")]
    assert '-n "$PREVIOUS"' in gate
    for flag in ("EVENTS_CHANGED", "RULES_CHANGED", "NEW_MIGRATIONS"):
        assert f'"${flag}" == 0' in gate
    rest = failed.split("\nfi\n", 1)[1]
    assert "automatic rollback is not safe" in rest
    assert "ops can-rollback" in rest


def test_a_manual_rollback_checks_can_rollback_and_runs_no_migration() -> None:
    """Д-§6.3: `deploy.sh <previous sha>` asks `ops can-rollback` and skips dump and migrations."""
    assert "ROLLBACK=1" in TEXT
    assert "can-rollback --to-registry" in TEXT
    assert "if ((!ROLLBACK && NEW_MIGRATIONS)); then" in TEXT
