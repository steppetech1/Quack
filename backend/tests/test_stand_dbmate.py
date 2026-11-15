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
    # Roles on the superuser DSN, then `dbmate up` as `quack_migrator` (WP9.1).
    assert migrate["command"] == ["ops", "migrate", "--with-roles"]
    assert migrate["restart"] == "no"
    assert migrate["depends_on"]["postgres"]["condition"] == "service_healthy"
    for name in PROCESSES:
        waits = compose["services"][name]["depends_on"]["migrate"]
        assert waits["condition"] == "service_completed_successfully", name


def _dsn_user(service: dict[str, Any]) -> str:
    dsn = str(service["environment"]["POSTGRES_DSN"])
    return dsn.split("://", 1)[1].split(":", 1)[0]


def test_the_services_do_not_connect_to_postgres_as_the_superuser(compose: dict[str, Any]) -> None:
    """WP9.1, Д-§7: the long-lived processes are `quack_app`, the short-lived `backup` and
    `alerts` are `quack_reader`; the superuser DSN stays with `migrate` (the roles step)."""
    services = compose["services"]
    for name in PROCESSES:
        assert _dsn_user(services[name]) == "quack_app", name
        assert "APP_DB_PASSWORD" in services[name]["environment"]["POSTGRES_DSN"], name
    for name in ("backup", "alerts"):
        assert _dsn_user(services[name]) == "quack_reader", name
    superusers = {
        name
        for name, service in services.items()
        if "POSTGRES_DSN" in service.get("environment", {}) and _dsn_user(service) == "quack"
    }
    assert superusers == {"migrate"}
    for name in ("APP_DB_PASSWORD", "READER_DB_PASSWORD", "MIGRATOR_DB_PASSWORD"):
        assert name in services["migrate"]["environment"], name


def test_a_local_env_without_the_role_passwords_falls_back_to_postgres_password() -> None:
    """Locally `docker compose up` works with the two passwords of the README; the server's
    `.env` is guarded by render-env.sh and deploy.sh."""
    text = COMPOSE.read_text(encoding="utf-8")
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in ("APP_DB_PASSWORD", "READER_DB_PASSWORD", "MIGRATOR_DB_PASSWORD"):
        assert f"${{{name}:-${{POSTGRES_PASSWORD" in text, name
        # Named in the example, with no value: a local stack falls back to POSTGRES_PASSWORD.
        assert re.search(rf"^# {name}=$", example, re.MULTILINE), name


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


def test_role_dsn_swaps_the_user_and_encodes_the_password() -> None:
    dsn = commands.role_dsn(
        "postgresql://quack:pw@postgres:5432/quack", "quack_migrator", "a/b@c:d$e"
    )

    assert dsn == "postgresql://quack_migrator:a%2Fb%40c%3Ad%24e@postgres:5432/quack"
    kept = commands.role_dsn("postgres://u:p@[::1]:6543/db?sslmode=require", "r", "x")
    assert kept == "postgres://r:x@[::1]:6543/db?sslmode=require"
    with pytest.raises(ValueError, match="postgresql://"):
        commands.role_dsn("host=postgres user=quack", "r", "x")


def _with_role_passwords(dsn: str, migrator: str = "m/pass") -> Settings:
    return _settings(dsn).model_copy(
        update={
            "app_db_password": SecretStr("app-pass"),
            "reader_db_password": SecretStr("reader-pass"),
            "migrator_db_password": SecretStr(migrator),
        }
    )


def test_migrate_with_roles_prepares_the_roles_and_runs_dbmate_as_the_migrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quack import cli

    steps: list[str] = []

    async def fake_roles(settings: Settings) -> int:
        steps.append(f"roles as {settings.postgres_dsn.split('@')[0]}")
        return 0

    def fake_migrate(settings: Settings) -> None:
        steps.append(f"dbmate as {settings.postgres_dsn.split('@')[0]}")

    monkeypatch.setattr(cli, "_db_roles", fake_roles)
    monkeypatch.setattr(cli, "migrate", fake_migrate)
    monkeypatch.setattr(cli, "ensure_clean_start", lambda dsn: steps.append("alembic check"))
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: _with_role_passwords("postgresql://quack:pw@postgres:5432/quack"),
    )

    assert cli.main(["ops", "migrate", "--with-roles"]) == 0
    assert steps == [
        "alembic check",
        "roles as postgresql://quack:pw",
        "dbmate as postgresql://quack_migrator:m%2Fpass",
    ]


def test_migrate_with_roles_stops_before_dbmate_when_a_password_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from quack import cli

    def never(settings: Settings) -> None:
        raise AssertionError("dbmate must not run without the roles")

    monkeypatch.setattr(cli, "migrate", never)
    monkeypatch.setattr(cli, "ensure_clean_start", lambda dsn: None)
    monkeypatch.setattr(
        cli, "get_settings", lambda: _settings("postgresql://quack:pw@postgres:5432/quack")
    )

    assert cli.main(["ops", "migrate", "--with-roles"]) == 1


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
