"""T-OPS1 (ТЗ Фазы 4): `compose.yaml` keeps the contract of Д-§3, Д-§4.1, Д-§4.2, Д-§6.1, Д-§10."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast

import pytest
import yaml  # pyright: ignore[reportMissingModuleSource]

COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"

SERVICES = {
    "caddy",
    "api",
    "workers",
    "projector",
    "compute",
    "postgres",
    "neo4j",
    "redis",
    "migrate",
    "backup",
    "alerts",
    "meta-mock",
}
# Д-§6.1 table.
STOP_GRACE_S = {"api": 35, "workers": 25, "projector": 15, "compute": 10}
BACKEND_ONLY = {"projector", "compute", "postgres", "neo4j", "redis", "migrate"}


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    return cast("dict[str, Any]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))


def _bytes(value: str) -> int:
    default = re.fullmatch(r"\$\{[^:}]+:-([^}]+)\}", value)
    text = default.group(1) if default else value
    number, unit = re.fullmatch(r"(\d+(?:\.\d+)?)([mg])", text).groups()  # pyright: ignore[reportOptionalMemberAccess]
    return int(float(number) * (1024**2 if unit == "m" else 1024**3))


def _seconds(value: str) -> int:
    return int(value.rstrip("s"))


def test_services_are_the_documented_ones(compose: dict[str, Any]):
    assert set(compose["services"]) == SERVICES


def test_only_caddy_publishes_ports(compose: dict[str, Any]):
    published = {n for n, s in compose["services"].items() if s.get("ports")}
    assert published == {"caddy"}
    assert sorted(compose["services"]["caddy"]["ports"]) == ["443:443", "80:80"]


def test_networks_follow_d_3_2(compose: dict[str, Any]):
    assert compose["networks"]["backend"] == {"internal": True}
    for name, service in compose["services"].items():
        networks = set(service["networks"])
        assert "backend" in networks, name
        wants_internet = name in {"caddy", "api", "workers", "backup", "alerts"}
        assert ("egress" in networks) == wants_internet, name
    for name in BACKEND_ONLY:
        assert set(compose["services"][name]["networks"]) == {"backend"}


def test_limits_and_swap_follow_d_4_1(compose: dict[str, Any]):
    for name, service in compose["services"].items():
        assert "cpus" in service and "mem_limit" in service, name
        ratio = _bytes(str(service["memswap_limit"])) / _bytes(str(service["mem_limit"]))
        # Д-§4.1: memswap_limit = mem_limit x 1.25 (rounded to whole MB in the table).
        assert ratio == pytest.approx(1.25, abs=0.01), name
    for name in ("postgres", "compute"):
        assert compose["services"][name]["shm_size"] == "256mb"


def test_stop_grace_period_follows_d_6_1(compose: dict[str, Any]):
    for name, seconds in STOP_GRACE_S.items():
        assert _seconds(compose["services"][name]["stop_grace_period"]) == seconds, name


def test_every_service_has_json_file_logs_10m_x_5(compose: dict[str, Any]):
    for name, service in compose["services"].items():
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "5"},
        }, name


def test_compute_gets_no_secrets_and_projector_no_model_key(compose: dict[str, Any]):
    compute = set(compose["services"]["compute"]["environment"])
    assert compute <= {"LOG_LEVEL"}
    projector = set(compose["services"]["projector"]["environment"])
    assert not projector & {"META_API_KEY", "COMPUTE_URL"}


def test_backup_is_short_lived_and_never_started_by_up(compose: dict[str, Any]):
    backup = compose["services"]["backup"]
    assert backup["restart"] == "no"
    assert backup["profiles"] == ["backup"]
    assert backup["command"] == ["ops", "backup"]


def _holders(compose: dict[str, Any], name: str) -> set[str]:
    return {n for n, s in compose["services"].items() if name in s.get("environment", {})}


def test_secrets_reach_only_the_services_of_d_7(compose: dict[str, Any]):
    """Д-§7: a service gets a variable only when its table row names it (T-OPS6)."""
    assert _holders(compose, "EMAIL_API_KEY") == {"api"}
    assert _holders(compose, "GOOGLE_OIDC_CLIENT_ID") == {"api"}
    assert _holders(compose, "GOOGLE_OIDC_CLIENT_SECRET") == {"api"}
    assert _holders(compose, "MODAL_TOKEN_ID") == {"workers"}
    assert _holders(compose, "MODAL_TOKEN_SECRET") == {"workers"}
    for name in ("S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY"):
        assert _holders(compose, name) == {"api", "workers"}, name
    for name in ("DELETIONS_S3_ACCESS_KEY", "DELETIONS_S3_SECRET_KEY"):
        assert _holders(compose, name) == {"api", "workers"}, name
    assert _holders(compose, "META_API_KEY") == {"api", "workers"}
    assert _holders(compose, "BACKUP_S3_ACCESS_KEY") == {"backup"}
    assert _holders(compose, "NEO4J_PASSWORD") == {"api", "workers", "projector"}


def test_alerts_is_short_lived_and_holds_no_model_or_storage_secret(compose: dict[str, Any]):
    alerts = compose["services"]["alerts"]
    assert alerts["restart"] == "no"
    assert alerts["profiles"] == ["alerts"]
    assert alerts["command"] == ["ops", "alerts"]
    assert _holders(compose, "HEARTBEAT_ALERTS_URL") == {"alerts"}
    assert _holders(compose, "ALERT_WEBHOOK_URL") == {"backup", "alerts"}
    assert not set(alerts["environment"]) & {"META_API_KEY", "NEO4J_PASSWORD", "S3_ACCESS_KEY"}
    assert all(v.endswith(":ro") for v in alerts["volumes"])


def test_the_learners_bucket_name_reaches_api_and_workers(compose: dict[str, Any]):
    assert _holders(compose, "S3_BUCKET_LEARNERS") == {"api", "workers"}


def test_the_price_table_reaches_the_processes_that_call_models(compose: dict[str, Any]):
    assert _holders(compose, "LLM_PRICES") == {"api", "workers"}
