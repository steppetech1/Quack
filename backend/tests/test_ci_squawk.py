"""Д-§5.2 п.2 (ТЗ Ф4 WP1.4, WP9.3; D-C-142): the CI job that runs squawk over new migrations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml  # pyright: ignore[reportMissingModuleSource]

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
# What Д-§5.2 п.2 names: blocking ALTERs and indexes without CONCURRENTLY — never excluded.
IN_SCOPE = {
    "require-concurrent-index-creation",
    "require-concurrent-index-deletion",
    "ban-concurrent-index-creation-in-transaction",
    "adding-field-with-default",
    "adding-not-nullable-field",
    "changing-column-type",
    "constraint-missing-not-valid",
    "adding-foreign-key-constraint",
    "disallowed-unique-constraint",
}


def _jobs() -> dict[str, Any]:
    return cast(dict[str, Any], yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"])


def _script(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_squawk_job_checks_new_migrations_with_the_config() -> None:
    job = _jobs()["squawk"]
    version = tuple(int(part) for part in job["env"]["SQUAWK_VERSION"].split("."))
    assert version >= (2, 51, 0)
    script = _script(job)
    assert "--config backend/.squawk.toml" in script
    assert "backend/db/migrations/*.sql" in script and "--diff-filter=AM" in script
    excluded = {
        rule
        for line in script.splitlines()
        if line.strip().startswith("excluded=")
        for rule in line.split("=", 1)[1].split(",")
    }
    assert excluded and not excluded & IN_SCOPE


def test_publishing_waits_for_squawk() -> None:
    """Д-§5.2: nothing merges or publishes until every check passes."""
    assert "squawk" in _jobs()["publish"]["needs"]
