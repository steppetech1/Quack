"""`ops alerts`: the check of Д-§9.3, run by the host timer every 5 minutes (ТЗ Фазы 4 WP9.5).

`conditions` is a pure function of a `Snapshot` (the `ops status` report, a probe of `/healthz`,
the file the host timer writes with containers and disk, the age of the newest local dump). The
time a condition has held, and when it was last announced, live in Redis (`AlertState`), because
every run is a fresh container. A condition is announced when it has held for its `hold_s` and is
repeated no more often than `alert_renotify_s`. After the run the heartbeat URL is pinged, so a
dead server or timer is noticed from outside (Д-§9.1).

Not yet here, their data does not exist: the objects of `quack-deletions` without a row in
`ops.deletion_log` (WP25) and the Contabo payment date. A failed backup is announced by `ops
backup` itself.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx
from redis.asyncio import Redis

from quack.config import Settings
from quack.logging import get_logger
from quack.ops.notify import post_alert

log = get_logger(__name__)

type Level = Literal["critical", "warning"]

STATE_TTL_S = 7 * 24 * 3600
HTTP_TIMEOUT_S = 5.0

# Keys of the conditions; `since` and `notified` of a key that is no longer true are cleared.
HEALTHZ_DOWN = "healthz_down"
PROJECTOR_STOPPED = "projector_stopped"
BACKUP_STALE = "backup_stale"
HOST_STATE_STALE = "host_state_stale"
RESTART_LOOP = "restart_loop:"
DISK_CRIT = "disk_crit"
DISK_WARN = "disk_warn"
PROJECTOR_LAG = "projector_lag"
QUEUE_OLD = "queue_old"
JOBS_FAILING = "jobs_failing"
PRESSURE = "model_pressure"
CIRCUIT_OPEN = "circuit_open"
TEAM_SPEND = "team_spend"


@dataclass(frozen=True)
class Alert:
    key: str
    level: Level
    message: str
    hold_s: float = 0.0


@dataclass(frozen=True)
class Snapshot:
    status: Mapping[str, Any]  # the `ops status` report
    healthz_ok: bool
    host: Mapping[str, Any] | None  # file of the host timer, None when it cannot be read
    last_backup_age_s: float | None  # None: no local dump yet
    previous_restarts: Mapping[str, int] = field(default_factory=dict)
    now: float = 0.0


def _dig(report: Mapping[str, Any], *path: str) -> Any:
    value: Any = report
    for part in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(part)  # pyright: ignore[reportUnknownMemberType]
    return value


def conditions(snapshot: Snapshot, settings: Settings) -> list[Alert]:
    """Every condition of the table in Д-§9.3 that is true now."""
    status = snapshot.status
    found: list[Alert] = []

    if not snapshot.healthz_ok:
        found.append(
            Alert(
                HEALTHZ_DOWN,
                "critical",
                "/healthz does not answer",
                hold_s=settings.alert_healthz_hold_s,
            )
        )

    stopped = _dig(status, "projector", "stopped_streams")
    if stopped:
        found.append(
            Alert(
                PROJECTOR_STOPPED,
                "critical",
                f"{stopped} projector stream(s) stopped: those learners get no memory updates",
            )
        )

    limit_s = settings.backup_interval_hours * 3600 + settings.alert_backup_grace_min * 60
    age = snapshot.last_backup_age_s
    if age is None:
        found.append(Alert(BACKUP_STALE, "critical", "no successful backup found: RPO is broken"))
    elif age > limit_s:
        found.append(
            Alert(
                BACKUP_STALE,
                "critical",
                f"last successful backup is {age / 3600:.1f} h old (limit "
                f"{limit_s / 3600:.1f} h): RPO is broken",
            )
        )

    found.extend(_host_conditions(snapshot, settings))

    lagging = _dig(status, "projector", "learners_lagging")
    max_lag = _dig(status, "projector", "max_lag_events")
    if lagging:
        found.append(
            Alert(
                PROJECTOR_LAG,
                "warning",
                f"projector lags for {lagging} learner(s), max {max_lag} events",
                hold_s=settings.alert_projector_lag_hold_s,
            )
        )

    oldest = _dig(status, "jobs", "oldest_queued_age_s")
    if oldest is not None and oldest > settings.alert_queue_age_s:
        found.append(
            Alert(QUEUE_OLD, "warning", f"the oldest queued job waits {oldest / 60:.0f} min")
        )

    share = _dig(status, "jobs", "failed_share_last_hour")
    if share is not None and share > settings.alert_failed_share:
        found.append(
            Alert(JOBS_FAILING, "warning", f"{share:.0%} of the jobs of the last hour failed")
        )

    pressure = _dig(status, "llm", "pressure")
    if pressure:
        found.append(
            Alert(
                PRESSURE,
                "warning",
                f"Meta API pressure flag is set for {', '.join(pressure)}: we hit the limits",
                hold_s=settings.alert_pressure_hold_s,
            )
        )

    spent = _dig(status, "llm", "cost_last_day_usd")
    if spent is not None and spent > settings.team_daily_alert_usd:
        found.append(
            Alert(
                TEAM_SPEND,
                "warning",
                f"the team spent ${spent:.0f} on models in the last day "
                f"(threshold ${settings.team_daily_alert_usd:.0f})",
            )
        )

    circuit = _dig(status, "llm", "circuit_open")
    if circuit:
        found.append(
            Alert(
                CIRCUIT_OPEN,
                "critical",
                f"model circuit breaker is open for {', '.join(circuit)}: the teacher answers "
                "with an error and a retry button",
            )
        )
    return found


def _host_conditions(snapshot: Snapshot, settings: Settings) -> list[Alert]:
    host = snapshot.host
    generated = _dig(host, "generated_at_epoch") if host is not None else None
    if (
        host is None
        or generated is None
        or snapshot.now - float(generated) > (settings.alert_host_state_max_age_s)
    ):
        return [
            Alert(
                HOST_STATE_STALE,
                "warning",
                "the host state file is missing or old: containers and disk are not watched",
            )
        ]
    found: list[Alert] = []
    for container in host.get("containers", []):
        name = str(container.get("name"))
        count = int(container.get("restart_count", 0))
        before = snapshot.previous_restarts.get(name)
        looping = container.get("state") == "restarting" or (
            before is not None and count - before >= settings.alert_restart_loop_delta
        )
        if looping:
            found.append(
                Alert(f"{RESTART_LOOP}{name}", "critical", f"container {name} restarts in a loop")
            )
    used = host.get("disk_used_pct")
    if used is not None:
        if used >= settings.disk_crit_pct:
            found.append(Alert(DISK_CRIT, "critical", f"disk is {used}% full"))
        elif used >= settings.disk_warn_pct:
            found.append(Alert(DISK_WARN, "warning", f"disk is {used}% full"))
    return found


class AlertState(Protocol):
    async def since(self, key: str) -> float | None: ...
    async def set_since(self, key: str, at: float) -> None: ...
    async def last_notified(self, key: str) -> float | None: ...
    async def set_notified(self, key: str, at: float) -> None: ...
    async def forget(self, key: str) -> None: ...
    async def known_keys(self) -> set[str]: ...
    async def restarts(self) -> dict[str, int]: ...
    async def set_restarts(self, counts: Mapping[str, int]) -> None: ...


class MemoryAlertState:
    """State of one process; the tests use it, and so does a run without Redis."""

    def __init__(self) -> None:
        self._since: dict[str, float] = {}
        self._notified: dict[str, float] = {}
        self._restarts: dict[str, int] = {}

    async def since(self, key: str) -> float | None:
        return self._since.get(key)

    async def set_since(self, key: str, at: float) -> None:
        self._since[key] = at

    async def last_notified(self, key: str) -> float | None:
        return self._notified.get(key)

    async def set_notified(self, key: str, at: float) -> None:
        self._notified[key] = at

    async def forget(self, key: str) -> None:
        self._since.pop(key, None)
        self._notified.pop(key, None)

    async def known_keys(self) -> set[str]:
        return set(self._since) | set(self._notified)

    async def restarts(self) -> dict[str, int]:
        return dict(self._restarts)

    async def set_restarts(self, counts: Mapping[str, int]) -> None:
        self._restarts = dict(counts)


class RedisAlertState:
    PREFIX = "alerts:"

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def _get(self, name: str) -> str | None:
        value = await self._redis.get(self.PREFIX + name)
        if value is None:
            return None
        return value.decode() if isinstance(value, bytes) else str(value)

    async def since(self, key: str) -> float | None:
        value = await self._get(f"since:{key}")
        return None if value is None else float(value)

    async def set_since(self, key: str, at: float) -> None:
        await self._redis.set(f"{self.PREFIX}since:{key}", repr(at), ex=STATE_TTL_S)

    async def last_notified(self, key: str) -> float | None:
        value = await self._get(f"notified:{key}")
        return None if value is None else float(value)

    async def set_notified(self, key: str, at: float) -> None:
        await self._redis.set(f"{self.PREFIX}notified:{key}", repr(at), ex=STATE_TTL_S)

    async def forget(self, key: str) -> None:
        await self._redis.delete(f"{self.PREFIX}since:{key}", f"{self.PREFIX}notified:{key}")

    async def known_keys(self) -> set[str]:
        keys: set[str] = set()
        async for raw in self._redis.scan_iter(match=f"{self.PREFIX}*:*"):
            name = raw.decode() if isinstance(raw, bytes) else str(raw)
            _, kind, key = name.split(":", 2)
            if kind in ("since", "notified"):
                keys.add(key)
        return keys

    async def restarts(self) -> dict[str, int]:
        raw = await self._get("restarts")
        return {} if raw is None else {str(k): int(v) for k, v in json.loads(raw).items()}

    async def set_restarts(self, counts: Mapping[str, int]) -> None:
        await self._redis.set(f"{self.PREFIX}restarts", json.dumps(dict(counts)), ex=STATE_TTL_S)


type Notify = Callable[[Settings, str], Awaitable[None]]
type Ping = Callable[[str], Awaitable[None]]


async def evaluate_and_announce(
    snapshot: Snapshot,
    settings: Settings,
    state: AlertState,
    notify: Notify,
    *,
    persistent: bool = True,
) -> tuple[list[Alert], list[Alert]]:
    """Announce the conditions that held long enough. Returns (sent, failed to send).

    Without persistent state (`persistent=False`, Redis down) the hold times cannot be measured,
    so every condition is announced at once and again on every run."""
    now = snapshot.now
    active = conditions(snapshot, settings)
    sent: list[Alert] = []
    failed: list[Alert] = []
    for alert in active:
        if persistent:
            since = await state.since(alert.key)
            if since is None:
                since = now
                await state.set_since(alert.key, now)
            if now - since < alert.hold_s:
                continue
            last = await state.last_notified(alert.key)
            if last is not None and now - last < settings.alert_renotify_s:
                continue
        try:
            await notify(settings, f"{alert.level.upper()}: {alert.message}")
        except Exception as exc:  # the next run tries again: `notified` is not set
            log.error("alert_notify_failed", key=alert.key, error=str(exc))
            failed.append(alert)
            continue
        if persistent:
            await state.set_notified(alert.key, now)
        sent.append(alert)
    if persistent:
        live = {alert.key for alert in active}
        for key in await state.known_keys() - live:
            await state.forget(key)
    return sent, failed


async def probe_healthz(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
            return (await http.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


def read_host_state(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None  # pyright: ignore[reportUnknownVariableType]


def newest_dump_age_s(directory: Path, now: float) -> float | None:
    try:
        newest = max(p.stat().st_mtime for p in directory.glob("quack-*.dump"))
    except (OSError, ValueError):  # no directory, or no dump in it
        return None
    return max(now - newest, 0.0)


async def _ping(url: str) -> None:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
        (await http.get(url)).raise_for_status()


async def run_alerts(
    settings: Settings,
    status: Mapping[str, Any],
    redis: Redis | None,
    *,
    notify: Notify = post_alert,
    ping: Ping = _ping,
    probe: Callable[[str], Awaitable[bool]] = probe_healthz,
    clock: Callable[[], float] = time.time,
) -> int:
    """One check. Exit code 0 when every announcement that was due was delivered, else 1."""
    now = clock()
    host = read_host_state(Path(settings.host_state_path))
    persistent = redis is not None
    state: AlertState = RedisAlertState(redis) if redis is not None else MemoryAlertState()
    try:
        previous = await state.restarts()
    except Exception as exc:
        log.error("alert_state_unavailable", error=str(exc))
        persistent, state, previous = False, MemoryAlertState(), {}
    snapshot = Snapshot(
        status=status,
        healthz_ok=await probe(settings.healthz_url),
        host=host,
        last_backup_age_s=newest_dump_age_s(Path(settings.backup_local_dir), now),
        previous_restarts=previous,
        now=now,
    )
    ok = True
    try:
        _, failed = await evaluate_and_announce(
            snapshot, settings, state, notify, persistent=persistent
        )
        ok = not failed
        if host is not None and persistent:
            await state.set_restarts(
                {str(c["name"]): int(c.get("restart_count", 0)) for c in host.get("containers", [])}
            )
    except Exception as exc:
        log.error("alerts_failed", error=str(exc))
        ok = False
    heartbeat = settings.heartbeat_alerts_url.get_secret_value()
    if heartbeat:
        try:
            await ping(heartbeat)
        except Exception as exc:  # the external service notices the missing ping (Д-§9.1)
            log.error("alerts_heartbeat_failed", error=str(exc))
    return 0 if ok else 1
