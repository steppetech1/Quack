"""The deploy job's .env (ТЗ Фазы 4 WP9.4, Д-§7): rendered from GitHub, compared by names only."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is not available")

REQUIRED = {
    "QUACK_REGISTRY": "ghcr.io/qvackteam",
    "POSTGRES_PASSWORD": "pg-secret-1",
    "NEO4J_PASSWORD": "neo-secret-2",
    "META_API_KEY": "meta-secret-3",
    "APP_DB_PASSWORD": "app-secret-4",
    "READER_DB_PASSWORD": "reader-secret-5",
    "MIGRATOR_DB_PASSWORD": "migrator-secret-6",
}


def template_names() -> list[str]:
    names: list[str] = []
    for line in (DEPLOY / "env.example").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=", line)
        if match:
            names.append(match.group(1))
    return names


def run(script: str, *args: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    clean = {"PATH": os.environ["PATH"], **env}
    return subprocess.run(
        [BASH, script, *args],
        cwd=DEPLOY,
        env=clean,
        capture_output=True,
        text=True,
        check=False,
    )


def test_render_follows_the_template_and_takes_values_from_the_environment():
    done = run("render-env.sh", "env.example", env={**REQUIRED, "EMAIL_API_KEY": "mail-secret-7"})

    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert [line.split("=", 1)[0] for line in lines] == template_names()
    values = dict(line.split("=", 1) for line in lines)
    assert values["POSTGRES_PASSWORD"] == "pg-secret-1"
    assert values["EMAIL_API_KEY"] == "mail-secret-7"
    assert values["ENV"] == "prod"  # the default of the template
    assert values["BACKUP_S3_ENDPOINT"] == ""  # optional, empty


def test_an_empty_required_value_fails_and_names_it_without_any_value():
    env = {**REQUIRED, "POSTGRES_PASSWORD": ""}

    done = run("render-env.sh", "env.example", env=env)

    assert done.returncode == 1
    assert "required POSTGRES_PASSWORD is empty" in done.stderr
    assert not any(secret in done.stderr for secret in REQUIRED.values())  # stdout is the .env


def test_a_missing_registry_keeps_the_placeholder_and_fails():
    env = {k: v for k, v in REQUIRED.items() if k != "QUACK_REGISTRY"}

    done = run("render-env.sh", "env.example", env=env)

    assert done.returncode == 1
    assert "QUACK_REGISTRY" in done.stderr


def test_a_value_with_a_newline_is_refused_and_not_echoed():
    env = {**REQUIRED, "EMAIL_API_KEY": "line1\nline2-secret"}

    done = run("render-env.sh", "env.example", env=env)

    assert done.returncode == 1
    assert "EMAIL_API_KEY contains a newline" in done.stderr
    assert "line2-secret" not in done.stderr


def test_a_template_with_crlf_line_endings_renders_the_same(tmp_path: Path):
    crlf = tmp_path / "env.crlf"
    crlf.write_bytes(
        (DEPLOY / "env.example").read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    )

    done = run("render-env.sh", str(crlf), env=REQUIRED)

    assert done.returncode == 0, done.stderr
    assert "\r" not in done.stdout


def test_env_diff_lists_names_only(tmp_path: Path):
    old, new = tmp_path / "old.env", tmp_path / "new.env"
    old.write_text("POSTGRES_PASSWORD=old-secret\nKEPT=same\nGONE=x\n", encoding="utf-8")
    new.write_text("POSTGRES_PASSWORD=new-secret\nKEPT=same\nADDED=y\n", encoding="utf-8")

    done = run("env-diff.sh", str(old), str(new), env={})

    assert done.returncode == 0, done.stderr
    assert sorted(done.stdout.splitlines()) == [
        "added:   ADDED",
        "changed: POSTGRES_PASSWORD",
        "removed: GONE",
    ]
    assert "secret" not in done.stdout + done.stderr


def test_env_diff_reports_no_differences_and_a_missing_old_file(tmp_path: Path):
    same = tmp_path / "same.env"
    same.write_text("A=1\n", encoding="utf-8")

    assert run("env-diff.sh", str(same), str(same), env={}).stdout.strip() == "no differences"
    assert run("env-diff.sh", str(tmp_path / "none.env"), str(same), env={}).stdout.strip() == (
        "added:   A"
    )


def test_deploy_workflow_maps_every_name_of_the_template():
    """A name missing from the render step would silently keep the template's default."""
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    mapped = set(
        re.findall(r"^\s+([A-Z][A-Z0-9_]*): \$\{\{ (?:vars|secrets)\.", text, re.MULTILINE)
    )
    missing = [name for name in template_names() if name not in mapped]
    assert missing == []
