"""CLI of Phase 3 and 4 (ТЗ Фазы 3 §8.6): T-O4. T-O1 (`ops status`) and `seed-learners` are in
`test_ops_p4_status.py` and `test_ops_p4_seed.py` (WP1.3 step 3); the registry is in
`test_ops_p4_registry.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from quack import cli
from quack.config import Settings

# --- T-O4: CLI of Phase 3 --------------------------------------------------------------


def test_cli_parser_phase3(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """§6.7, WP8.1: process roles and the ops commands with their arguments; `worker` (both
    roles in one process, development only) refuses to start on prod with exit code 2."""
    parser = cli.build_parser()
    for argv in (["api"], ["projector"], ["workers"], ["worker"]):
        assert parser.parse_args(argv).command == argv[0]
    seed = parser.parse_args(
        ["ops", "seed-learners", "--count", "3", "--out", "l.json", "--fixture", "x", "--confirm"]
    )
    assert (seed.op, seed.count, seed.out, seed.fixture, seed.confirm) == (
        "seed-learners",
        3,
        "l.json",
        "x",
        True,
    )
    assert parser.parse_args(["ops", "openapi", "--out", "o.json"]).out == "o.json"
    # The commands of Phase 3 over `ops.dlq` and the tombstones are gone (WP1.3 step 3).
    for removed in (["ops", "replay-dlq"], ["ops", "purge-deleted"]):
        with pytest.raises(SystemExit):
            parser.parse_args(removed)
    assert parser.parse_args(["ops", "status"]).op == "status"
    assert parser.parse_args(["ops", "event-registry", "--out", "r.json"]).out == "r.json"
    rollback = parser.parse_args(["ops", "can-rollback", "--to-registry", "prev.json"])
    assert rollback.to_registry == "prev.json"
    assert cli.PROCESS_ROLES == {
        "api": "api",
        "projector": "projector",
        "workers": "workers",
        "worker": "workers",
        "ops": "ops",
    }

    monkeypatch.setattr(cli, "get_settings", lambda: settings.model_copy(update={"env": "prod"}))
    assert cli.main(["worker"]) == 2


def test_cli_binds_process_role_and_version(settings: Settings) -> None:
    """WP8.1 (Д-§9.1): the role goes into Settings before resources, every log line carries
    `process` and `version`."""
    import structlog

    configured = cli.process_settings(
        settings.model_copy(update={"quack_version": "1.2.3"}), "workers"
    )
    assert configured.process_role == "workers"
    try:
        bound = structlog.contextvars.get_contextvars()
        assert (bound["process"], bound["version"]) == ("workers", "1.2.3")
    finally:
        structlog.contextvars.unbind_contextvars("process", "version")


def test_cli_sets_process_role_before_settings(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """WP8.1, WP9.2: `Settings` is validated with the role of the command — prod secrets are
    those of the role (Д-§7), so `migrate` and `projector` start without JWT/Meta secrets."""
    import structlog

    monkeypatch.setenv("PROCESS_ROLE", "api")
    seen: list[str] = []

    def fake_settings() -> Settings:
        import os

        seen.append(os.environ["PROCESS_ROLE"])
        return settings

    monkeypatch.setattr(cli, "get_settings", fake_settings)
    try:
        assert cli.main(["ops", "event-registry", "--out", str(tmp_path / "r.json")]) == 0
    finally:
        structlog.contextvars.unbind_contextvars("process", "version")
    assert seen == ["ops"]
