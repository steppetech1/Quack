"""WP1.3 step 2 / WP9 (D-C-160, issue #223): the stand, compose and the rollout job bring up the
Phase 4 schema with dbmate before `api`, `projector` and `workers`, which run the processes of
Phase 4 unconditionally (the flag `PHASE4_PROCESSES` is removed, D-A-223)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # pyright: ignore[reportMissingModuleSource]
from pydantic import SecretStr

from quack.config import Settings
from quack.ops import commands

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"
DEPLOY_SH = ROOT / "deploy" / "deploy.sh"
DOCKERFILE = ROOT / "backend" / "Dockerfile"
CI = ROOT / ".github" / "workflows" / "ci.yml"
CT_SH = ROOT / "backend" / "scripts" / "dev" / "ct.sh"

PROCESSES = ("api", "projector", "workers")


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))


# --- compose -----------------------------------------------------------------------------


def test_the_processes_of_phase_4_need_no_flag(compose: dict[str, Any]) -> None:
    """The processes of Phase 4 are the only ones (D-A-223): no service sets the retired
    `PHASE4_PROCESSES`, and `Settings` has no field for it."""
    for name, service in compose["services"].items():
        assert "PHASE4_PROCESSES" not in service.get("environment", {}), name
    assert "phase4_processes" not in Settings.model_fields


def test_the_schema_is_migrated_before_the_processes_start(compose: dict[str, Any]) -> None:
    migrate = compose["services"]["migrate"]
    assert migrate["command"] == ["ops", "migrate"]
    assert migrate["restart"] == "no"
    assert migrate["depends_on"]["postgres"]["condition"] == "service_healthy"
    for name in PROCESSES:
        waits = compose["services"][name]["depends_on"]["migrate"]
        assert waits["condition"] == "service_completed_successfully", name


# --- image -------------------------------------------------------------------------------


def test_the_image_holds_dbmate_and_the_migrations_but_not_alembic() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.search(r"^COPY db \./db$", text, re.MULTILINE)
    assert "sha256sum -c" in text and "/usr/local/bin/dbmate" in text
    assert "alembic.ini" not in text and "COPY migrations" not in text


def _pins(text: str) -> tuple[str, str]:
    version = re.search(r"DBMATE_VERSION[=:] ?\"?(v[\d.]+)", text)
    sha = re.search(r"DBMATE_SHA256[=:] ?\"?([0-9a-f]{64})", text)
    assert version and sha
    return version.group(1), sha.group(1)


def test_dbmate_has_the_same_version_and_checksum_in_ci_image_and_dev_script() -> None:
    pins = {_pins(path.read_text(encoding="utf-8")) for path in (CI, DOCKERFILE, CT_SH)}
    assert len(pins) == 1, pins


# --- rollout -----------------------------------------------------------------------------


def test_deploy_reads_the_migrations_of_an_image_from_the_dbmate_directory() -> None:
    text = DEPLOY_SH.read_text(encoding="utf-8")
    assert "/app/db/migrations" in text
    assert "/app/migrations/versions" not in text and "alembic" not in text.lower()


def test_deploy_migrates_after_the_dump_and_before_it_starts_the_processes() -> None:
    text = DEPLOY_SH.read_text(encoding="utf-8")
    body = text[text.index("PREVIOUS=") :]
    positions = [
        body.index("ops db-roles"),
        body.index("ops backup now"),
        body.index("dc run --rm migrate; then"),
        body.index("\nrollout\n"),
    ]
    assert positions == sorted(positions)
    # The services are started one by one, without the dependencies of compose: the order above
    # is the only thing that holds the schema back.
    rollout = text[text.index("rollout() {") : text.index("PREVIOUS=")]
    assert rollout.count("--no-deps") >= 4 and "migrate" not in rollout


# --- `ops migrate` -----------------------------------------------------------------------


def _settings(dsn: str) -> Settings:
    values: dict[str, Any] = {"env": "test", "postgres_dsn": dsn, "meta_api_key": SecretStr("k")}
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_dbmate_url_adds_sslmode_disable_only_when_the_dsn_has_none() -> None:
    plain = commands.dbmate_url("postgresql://quack:pw@postgres:5432/quack")
    assert plain == "postgres://quack:pw@postgres:5432/quack?sslmode=disable"
    kept = commands.dbmate_url("postgresql://quack:pw@h:5432/quack?sslmode=require")
    assert kept == "postgres://quack:pw@h:5432/quack?sslmode=require"
    extra = commands.dbmate_url("postgres://u@h/db?connect_timeout=5")
    assert extra == "postgres://u@h/db?connect_timeout=5&sslmode=disable"


def test_ops_migrate_runs_dbmate_up_on_the_migrations_directory() -> None:
    calls: list[tuple[list[str], dict[str, str]]] = []

    def run(args: list[str], *, env: dict[str, str], check: bool):
        assert check is True
        calls.append((args, env))
        return subprocess.CompletedProcess(args, 0)

    commands.migrate(
        _settings("postgresql://quack:pw@postgres:5432/quack"),
        run=run,
        has_alembic_schema=lambda dsn: False,
    )

    ((args, env),) = calls
    assert args[0] == "dbmate" and args[-1] == "up"
    assert "--no-dump-schema" in args
    directory = args[args.index("--migrations-dir") + 1]
    assert Path(directory).as_posix().endswith("backend/db/migrations")
    assert (Path(directory) / "20261102000001_baseline.sql").is_file()
    # The password goes through the environment, never through the command line.
    assert env["DATABASE_URL"].endswith("@postgres:5432/quack?sslmode=disable")
    assert not any("pw@" in arg for arg in args)


def test_ops_migrate_refuses_a_database_with_the_alembic_schema() -> None:
    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("dbmate must not run on the schema of Phase 3")

    with pytest.raises(commands.AlembicSchemaFound, match="clean start"):
        commands.migrate(
            _settings("postgresql://quack:pw@postgres:5432/quack"),
            run=never,  # type: ignore[arg-type]
            has_alembic_schema=lambda dsn: True,
        )


def test_ops_migrate_reports_a_failed_dbmate() -> None:
    def run(args: list[str], *, env: dict[str, str], check: bool):
        raise subprocess.CalledProcessError(1, args)

    with pytest.raises(subprocess.CalledProcessError):
        commands.migrate(
            _settings("postgresql://quack:pw@postgres:5432/quack"),
            run=run,
            has_alembic_schema=lambda dsn: False,
        )
