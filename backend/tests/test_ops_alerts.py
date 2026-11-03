"""T-OPS5 (ТЗ Фазы 4): every condition of the table in Д-§9.3 gives a message of the right level."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from quack.config import Settings
from quack.ops import alerts
from quack.ops.alerts import (
    MemoryAlertState,
    Snapshot,
    conditions,
    evaluate_and_announce,
    newest_dump_age_s,
    run_alerts,
)

NOW = 1_800_000_000.0

HEALTHY_STATUS: dict[str, Any] = {
    "projector": {
        "learners_total": 3,
        "learners_lagging": 0,
        "max_lag_events": 0,
        "stopped_streams": 0,
    },
    "jobs": {"oldest_queued_age_s": None, "failed_share_last_hour": None},
    "llm": {"pressure": [], "circuit_open": []},
}
HEALTHY_HOST: dict[str, Any] = {
    "generated_at_epoch": NOW - 30,
    "disk_used_pct": 40,
    "containers": [{"name": "quack-api-1", "state": "running", "restart_count": 0}],
}


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def snapshot(
    *,
    status_patch: dict[str, dict[str, Any]] | None = None,
    host: dict[str, Any] | None = HEALTHY_HOST,
    healthz_ok: bool = True,
    backup_age_s: float | None = 3600.0,
    previous_restarts: dict[str, int] | None = None,
) -> Snapshot:
    status = deepcopy(HEALTHY_STATUS)
    for section, values in (status_patch or {}).items():
        status[section].update(values)
    return Snapshot(
        status=status,
        healthz_ok=healthz_ok,
        host=host,
        last_backup_age_s=backup_age_s,
        previous_restarts=previous_restarts or {},
        now=NOW,
    )


def by_key(snap: Snapshot, **values: object) -> dict[str, alerts.Alert]:
    return {a.key: a for a in conditions(snap, settings(**values))}


def test_a_healthy_system_has_no_condition():
    assert conditions(snapshot(), settings()) == []


def test_healthz_down_is_critical_and_needs_two_checks():
    found = by_key(snapshot(healthz_ok=False))
    assert found[alerts.HEALTHZ_DOWN].level == "critical"
    assert found[alerts.HEALTHZ_DOWN].hold_s == 300


def test_a_stopped_projector_stream_is_critical():
    found = by_key(snapshot(status_patch={"projector": {"stopped_streams": 2}}))
    assert found[alerts.PROJECTOR_STOPPED].level == "critical"
    assert "2" in found[alerts.PROJECTOR_STOPPED].message


@pytest.mark.parametrize("age_s", [None, 6 * 3600 + 31 * 60])
def test_an_old_or_missing_backup_is_critical(age_s: float | None):
    found = by_key(snapshot(backup_age_s=age_s))
    assert found[alerts.BACKUP_STALE].level == "critical"


def test_a_backup_inside_interval_plus_30_minutes_is_fine():
    assert alerts.BACKUP_STALE not in by_key(snapshot(backup_age_s=6 * 3600 + 29 * 60))


def test_a_restarting_container_is_critical():
    host = {
        **HEALTHY_HOST,
        "containers": [{"name": "w", "state": "restarting", "restart_count": 9}],
    }
    assert by_key(snapshot(host=host))[f"{alerts.RESTART_LOOP}w"].level == "critical"


def test_a_growing_restart_count_is_a_loop():
    host = {**HEALTHY_HOST, "containers": [{"name": "w", "state": "running", "restart_count": 5}]}
    assert f"{alerts.RESTART_LOOP}w" in by_key(snapshot(host=host, previous_restarts={"w": 1}))
    assert f"{alerts.RESTART_LOOP}w" not in by_key(snapshot(host=host, previous_restarts={"w": 4}))
    assert f"{alerts.RESTART_LOOP}w" not in by_key(snapshot(host=host))  # first run: no baseline


@pytest.mark.parametrize(
    ("used", "key", "level"),
    [
        (75, alerts.DISK_WARN, "warning"),
        (89, alerts.DISK_WARN, "warning"),
        (90, alerts.DISK_CRIT, "critical"),
    ],
)
def test_disk_thresholds(used: int, key: str, level: str):
    found = by_key(snapshot(host={**HEALTHY_HOST, "disk_used_pct": used}))
    assert found[key].level == level
    assert set(found) == {key}


def test_disk_below_75_is_fine():
    assert by_key(snapshot(host={**HEALTHY_HOST, "disk_used_pct": 74})) == {}


@pytest.mark.parametrize("host", [None, {**HEALTHY_HOST, "generated_at_epoch": NOW - 901}])
def test_a_missing_or_old_host_file_is_a_warning(host: dict[str, Any] | None):
    assert by_key(snapshot(host=host))[alerts.HOST_STATE_STALE].level == "warning"


def test_projector_lag_is_a_warning_after_ten_minutes():
    found = by_key(
        snapshot(status_patch={"projector": {"learners_lagging": 4, "max_lag_events": 70}})
    )
    assert found[alerts.PROJECTOR_LAG].level == "warning"
    assert found[alerts.PROJECTOR_LAG].hold_s == 600


def test_an_old_queued_job_is_a_warning():
    assert (
        by_key(snapshot(status_patch={"jobs": {"oldest_queued_age_s": 601.0}}))[
            alerts.QUEUE_OLD
        ].level
        == "warning"
    )
    assert alerts.QUEUE_OLD not in by_key(
        snapshot(status_patch={"jobs": {"oldest_queued_age_s": 600.0}})
    )


def test_more_than_ten_percent_failed_jobs_is_a_warning():
    assert (
        by_key(snapshot(status_patch={"jobs": {"failed_share_last_hour": 0.11}}))[
            alerts.JOBS_FAILING
        ].level
        == "warning"
    )
    assert alerts.JOBS_FAILING not in by_key(
        snapshot(status_patch={"jobs": {"failed_share_last_hour": 0.10}})
    )


def test_a_pressure_flag_is_a_warning_that_must_hold():
    found = by_key(snapshot(status_patch={"llm": {"pressure": ["muse-wonder-1.0"]}}))
    assert found[alerts.PRESSURE].level == "warning"
    assert found[alerts.PRESSURE].hold_s == 300


def test_an_open_circuit_breaker_is_critical():
    found = by_key(snapshot(status_patch={"llm": {"circuit_open": ["muse-wonder-1.0"]}}))
    assert found[alerts.CIRCUIT_OPEN].level == "critical"
    assert "muse-wonder-1.0" in found[alerts.CIRCUIT_OPEN].message


def test_thresholds_come_from_settings():
    snap = snapshot(host={**HEALTHY_HOST, "disk_used_pct": 60})
    assert by_key(snap, disk_warn_pct=50)[alerts.DISK_WARN].level == "warning"
    assert alerts.BACKUP_STALE in by_key(
        snapshot(backup_age_s=3 * 3600 + 1900), backup_interval_hours=3
    )


class Sent:
    def __init__(self, fail: bool = False) -> None:
        self.messages: list[str] = []
        self.fail = fail

    async def __call__(self, _settings: Settings, message: str) -> None:
        if self.fail:
            raise RuntimeError("chat down")
        self.messages.append(message)


def at(snap: Snapshot, seconds: float) -> Snapshot:
    """The same system `seconds` later; the host timer keeps writing its file."""
    host = None if snap.host is None else {**snap.host, "generated_at_epoch": NOW + seconds - 30}
    return Snapshot(**{**snap.__dict__, "now": NOW + seconds, "host": host})


async def test_a_held_condition_is_announced_only_after_its_hold_time():
    state, sent, cfg = MemoryAlertState(), Sent(), settings()
    down = snapshot(healthz_ok=False)

    first, _ = await evaluate_and_announce(down, cfg, state, sent)
    second, _ = await evaluate_and_announce(at(down, 299), cfg, state, sent)
    third, _ = await evaluate_and_announce(at(down, 300), cfg, state, sent)

    assert (first, second) == ([], [])
    assert [a.key for a in third] == [alerts.HEALTHZ_DOWN]
    assert sent.messages == ["CRITICAL: /healthz does not answer"]


async def test_an_announced_condition_repeats_only_after_the_renotify_time():
    state, sent, cfg = MemoryAlertState(), Sent(), settings()
    broken = snapshot(status_patch={"projector": {"stopped_streams": 1}})

    await evaluate_and_announce(broken, cfg, state, sent)
    await evaluate_and_announce(at(broken, 300), cfg, state, sent)
    await evaluate_and_announce(at(broken, 3600), cfg, state, sent)

    assert len(sent.messages) == 2


async def test_a_cleared_condition_starts_again_from_zero():
    state, sent, cfg = MemoryAlertState(), Sent(), settings()
    down = snapshot(healthz_ok=False)

    await evaluate_and_announce(down, cfg, state, sent)
    await evaluate_and_announce(at(snapshot(), 120), cfg, state, sent)  # healthy again
    await evaluate_and_announce(at(down, 400), cfg, state, sent)  # a new episode

    assert sent.messages == []  # hold time counted anew, 400 s after the first run is not enough
    assert await state.since(alerts.HEALTHZ_DOWN) == NOW + 400


async def test_a_failed_delivery_is_retried_and_reported():
    state, cfg = MemoryAlertState(), settings()
    broken = snapshot(status_patch={"projector": {"stopped_streams": 1}})

    sent, failed = await evaluate_and_announce(broken, cfg, state, Sent(fail=True))
    assert (sent, [a.key for a in failed]) == ([], [alerts.PROJECTOR_STOPPED])

    again = Sent()
    sent, failed = await evaluate_and_announce(at(broken, 300), cfg, state, again)
    assert [a.key for a in sent] == [alerts.PROJECTOR_STOPPED] and failed == []


async def test_without_state_every_condition_is_announced_at_once():
    sent, cfg = Sent(), settings()
    down = snapshot(healthz_ok=False)

    await evaluate_and_announce(down, cfg, MemoryAlertState(), sent, persistent=False)
    await evaluate_and_announce(at(down, 60), cfg, MemoryAlertState(), sent, persistent=False)

    assert len(sent.messages) == 2


def test_newest_dump_age(tmp_path: Path):
    assert newest_dump_age_s(tmp_path / "missing", NOW) is None
    assert newest_dump_age_s(tmp_path, NOW) is None
    old, new = tmp_path / "quack-1.dump", tmp_path / "quack-2.dump"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (NOW - 7200, NOW - 7200))
    os.utime(new, (NOW - 600, NOW - 600))
    assert newest_dump_age_s(tmp_path, NOW) == pytest.approx(600)


async def test_run_alerts_pings_the_heartbeat_and_exits_zero(tmp_path: Path):
    host = tmp_path / "state.json"
    host.write_text(json.dumps({**HEALTHY_HOST, "generated_at_epoch": NOW - 5}), encoding="utf-8")
    dumps = tmp_path / "dumps"
    dumps.mkdir()
    (dumps / "quack-1.dump").write_bytes(b"x")
    os.utime(dumps / "quack-1.dump", (NOW - 100, NOW - 100))
    pings: list[str] = []

    async def ping(url: str) -> None:
        pings.append(url)

    async def probe(_url: str) -> bool:
        return True

    code = await run_alerts(
        settings(
            host_state_path=str(host),
            backup_local_dir=str(dumps),
            heartbeat_alerts_url=SecretStr("https://hb.example/alerts"),
        ),
        HEALTHY_STATUS,
        None,
        notify=Sent(),
        ping=ping,
        probe=probe,
        clock=lambda: NOW,
    )

    assert code == 0
    assert pings == ["https://hb.example/alerts"]


async def test_run_alerts_exits_one_when_an_announcement_cannot_be_delivered(tmp_path: Path):
    async def probe(_url: str) -> bool:
        return False

    async def ping(_url: str) -> None:
        return None

    code = await run_alerts(
        settings(host_state_path=str(tmp_path / "none.json"), backup_local_dir=str(tmp_path)),
        HEALTHY_STATUS,
        None,
        notify=Sent(fail=True),
        ping=ping,
        probe=probe,
        clock=lambda: NOW,
    )

    assert code == 1
