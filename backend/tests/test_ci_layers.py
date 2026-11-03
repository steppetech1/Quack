"""Test layers in CI (D-C-146, backend/TESTS.md): `fast` everywhere, full `backend` on pull
requests and the integration branches, `compute` and `images` gated by the changed paths,
`publish` only after every check and only on main and phase-4."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml  # pyright: ignore[reportMissingModuleSource]

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
STREAM_BRANCHES = {"phase-4-a", "phase-4-b", "phase-4-c"}
CHECKS = {"fast", "backend", "squawk", "compute", "images"}


def _workflow() -> dict[Any, Any]:
    return cast(dict[Any, Any], yaml.safe_load(CI.read_text(encoding="utf-8")))


def _jobs() -> dict[str, Any]:
    return cast(dict[str, Any], _workflow()["jobs"])


def _steps_text(job: dict[str, Any]) -> str:
    return "\n".join(f"{step.get('name', '')}\n{step.get('run', '')}" for step in job["steps"])


def test_triggers_cover_pull_requests_and_branches() -> None:
    # PyYAML reads the key `on` as the boolean True.
    workflow = _workflow()
    triggers = cast(dict[str, Any], workflow["on"] if "on" in workflow else workflow[True])
    assert "pull_request" in triggers
    assert {"main", "phase-4"} | STREAM_BRANCHES <= set(triggers["push"]["branches"])
    assert "pull_request_target" not in triggers and "workflow_run" not in triggers


def test_fast_job_has_no_services_and_no_infra_requirement() -> None:
    job = _jobs()["fast"]
    assert "services" not in job
    assert "if" not in job, "the fast layer runs on every event"
    assert "QUACK_TEST_REQUIRE_INFRA" not in str(job)
    assert "QUACK_TEST_" not in str(job.get("env", {}))
    for step in job["steps"]:
        assert "QUACK_TEST_" not in str(step.get("env", {}))
    script = _steps_text(job)
    for command in (
        "uv sync --frozen",
        "ruff check .",
        "ruff format --check .",
        "pyright",
        "pytest -q",
    ):
        assert command in script


def test_full_job_has_services_and_requires_infra() -> None:
    job = _jobs()["backend"]
    assert {"postgres", "neo4j", "redis"} <= set(job["services"])
    assert job["env"]["QUACK_TEST_REQUIRE_INFRA"] == "1"
    script = _steps_text(job)
    assert "pytest -q" in script and "dbmate" in script
    # Lint and types are the fast layer's work.
    assert "ruff" not in script and "pyright" not in script


def test_full_job_does_not_run_on_stream_branch_pushes() -> None:
    condition = str(_jobs()["backend"]["if"])
    assert "pull_request" in condition
    assert "refs/heads/main" in condition and "refs/heads/phase-4'" in condition
    for branch in STREAM_BRANCHES:
        assert branch not in condition


def test_compute_job_always_reports_but_gates_heavy_steps_by_changed_files() -> None:
    job = _jobs()["compute"]
    assert "if" not in job, "a required check must always report"
    assert any(step.get("with", {}).get("fetch-depth") == 0 for step in job["steps"])
    gate = next(step for step in job["steps"] if step.get("id") == "changes")
    assert "github.event.pull_request.base.sha" in gate["env"]["BASE"]
    assert "github.event.before" in gate["env"]["BASE"]
    assert "git diff --name-only" in gate["run"]
    assert "compute=true" in gate["run"] and "no base commit" in gate["run"]
    heavy = [
        step
        for step in job["steps"]
        if step is not gate and "actions/checkout" not in str(step.get("uses", ""))
    ]
    assert heavy
    for step in heavy:
        assert step.get("if") == "steps.changes.outputs.compute == 'true'", step


def test_images_job_builds_only_when_backend_or_compute_changed() -> None:
    job = _jobs()["images"]
    gate = next(step for step in job["steps"] if step.get("id") == "changes")
    assert "backend/" in gate["run"] and "compute/" in gate["run"]
    builds = [step for step in job["steps"] if "build-push-action" in str(step.get("uses", ""))]
    assert len(builds) == 2
    for step in builds:
        assert step.get("if") == "steps.changes.outputs.images == 'true'"
        assert step["with"]["push"] is False


def test_publish_waits_for_every_check_and_only_on_integration_pushes() -> None:
    job = _jobs()["publish"]
    assert set(job["needs"]) == CHECKS
    condition = str(job["if"])
    assert "github.event_name == 'push'" in condition
    assert "refs/heads/main" in condition and "refs/heads/phase-4'" in condition
    for branch in STREAM_BRANCHES:
        assert branch not in condition
    # Only the publishing job writes packages.
    assert _workflow()["permissions"] == {"contents": "read"}
    for name, other in _jobs().items():
        if name != "publish":
            assert "packages" not in other.get("permissions", {})


def test_actions_are_pinned_by_commit_sha() -> None:
    for job in _jobs().values():
        # A job that calls a workflow of this repository (`uses: ./...`) has no steps and no pin.
        for step in job.get("steps", []):
            uses = step.get("uses")
            if uses:
                ref = str(uses).split("@", 1)[1]
                assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), uses


def test_deploy_follows_publish_on_main_only_where_enabled() -> None:
    job = _jobs()["deploy"]
    assert job["needs"] == ["publish"]
    assert job["uses"] == "./.github/workflows/deploy.yml"
    condition = str(job["if"])
    assert "refs/heads/main" in condition and "github.event_name == 'push'" in condition
    assert "vars.DEPLOY_ENABLED == 'true'" in condition
    assert "phase-4" not in condition
    assert job["permissions"] == {"contents": "read"}
