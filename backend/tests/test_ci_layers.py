"""Test layers in CI (D-C-146, backend/TESTS.md): `fast` everywhere, full `backend` on pull
requests and the integration branches, `compute` and `images` gated by the changed paths,
`publish` only after every check and only on main and phase-4."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import yaml  # pyright: ignore[reportMissingModuleSource]

CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
DEPLOY = CI.with_name("deploy.yml")
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


def test_fast_job_checks_the_event_registry_gaps() -> None:
    """WP9.3, Д-§5.2 п.6 (#232, D-C-166): the step «реестр событий» is `registry_gaps` of
    Phase 4; it needs no services, so it sits in the fast layer, and a gap fails the job."""
    steps = [s for s in _jobs()["fast"]["steps"] if "registry_gaps" in str(s.get("run", ""))]
    (step,) = steps
    assert "реестр событий" in step["name"]
    run = step["run"]
    assert "quack.runtime.event_registry import registry_gaps" in run
    assert "sys.exit(1 if gaps else 0)" in run
    assert "quack.ops.registry" not in _steps_text(_jobs()["fast"])


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


def test_fast_job_checks_modal_with_the_backend_settings() -> None:
    """WP9.3: `modal/` has its ruff and pyright (D-C-210); it has no project of its own, so the
    backend's settings and environment (`modal` is a backend dependency, §4.6) are used."""
    (step,) = [s for s in _jobs()["fast"]["steps"] if "modal/" in str(s.get("name", ""))]
    run = step["run"]
    for command in (
        "ruff check --config pyproject.toml ../modal",
        "ruff format --check --config pyproject.toml ../modal",
        "pyright ../modal",
    ):
        assert command in run


def test_isolation_and_one_writer_are_steps_of_their_own() -> None:
    """Д-§5.2 п.5 (D-C-210): the contract tests run before the full run as named steps, with
    the infrastructure of the `backend` job (T-DB4, T-E3, T-A9a, T-A9b)."""
    steps = _jobs()["backend"]["steps"]
    names = [str(step.get("name", "")) for step in steps]
    isolation = next(i for i, n in enumerate(names) if n.startswith("Isolation"))
    writer = next(i for i, n in enumerate(names) if n.startswith("One writer"))
    full = next(i for i, step in enumerate(steps) if step.get("run") == "uv run pytest -q")
    assert isolation < full
    assert writer < full
    assert "test_repository_functions_take_learner_id" in steps[isolation]["run"]
    assert "test_cross_learner_isolation" in steps[isolation]["run"]
    assert "test_neo4j_writes_only_from_projector_static" in steps[writer]["run"]
    assert "test_read_session_rejects_writes" in steps[writer]["run"]


def test_frontend_types_check_is_on_pull_requests_and_not_a_gate_of_publish() -> None:
    """Д-§5.2 п.7, ТЗ Ф4 WP9.3 (R4-2, D-C-210): types generated from `backend/openapi.json`
    equal the committed ones; expectedly red until the frontend moves, so `publish` does not
    wait for it, and it changes no file of the frontend."""
    job = _jobs()["frontend-types"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert "frontend-types" not in _jobs()["publish"]["needs"]
    assert "permissions" not in job
    assert "secrets" not in str(job)
    script = _steps_text(job)
    assert "npm ci --ignore-scripts" in script
    assert "openapi-typescript backend/openapi.json" in script
    assert "frontend/lib/api/schema.d.ts" in script
    assert "cmp " in script
    assert "api:types" in script, "flags are read from the frontend's own script"


def test_frontend_types_script_keeps_the_shape_the_ci_step_expects() -> None:
    package = json.loads((CI.parents[2] / "frontend" / "package.json").read_text(encoding="utf-8"))
    script = package["scripts"]["api:types"]
    assert script.startswith("openapi-typescript lib/api/openapi.json -o lib/api/schema.d.ts")


def test_deploy_job_runs_in_production_one_at_a_time_and_stops_on_freeze() -> None:
    """ТЗ Ф4 WP9.3/WP9.4: environment `production`, `concurrency: deploy`, `DEPLOY_FREEZE = true`
    skips every step after the freeze check (D-C-210)."""
    workflow = cast(dict[Any, Any], yaml.safe_load(DEPLOY.read_text(encoding="utf-8")))
    job = workflow["jobs"]["deploy"]
    assert job["environment"] == "production"
    assert job["concurrency"] == {"group": "deploy", "cancel-in-progress": False}
    assert job["env"]["FROZEN"] == "${{ vars.DEPLOY_FREEZE }}"
    steps = job["steps"]
    assert steps[0]["id"] == "freeze"
    assert 'FROZEN" == "true"' in steps[0]["run"]
    for step in steps[1:]:
        assert "steps.freeze.outputs.skip != 'true'" in str(step.get("if")), step
    triggers = workflow["on"] if "on" in workflow else workflow[True]
    assert "pull_request" not in triggers
    assert "pull_request_target" not in triggers
    assert workflow["permissions"] == {"contents": "read"}
