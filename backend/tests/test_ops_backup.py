"""T-OPS7 (ТЗ Фазы 4): `ops backup` with substituted processes and a directory instead of R2."""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import SecretStr

from quack.config import Settings
from quack.ops.backup import BackupCommands, DirUploader, run_backup

DUMP = b"dump-bytes-" * 200_000  # larger than one read chunk

FAKE_DUMP = (sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'dump-bytes-' * 200000)")
FAKE_AGE = (
    sys.executable,
    "-c",
    "import sys; sys.stdout.buffer.write(b'AGE:' + sys.stdin.buffer.read())",
)
# 300 KB of warnings on stderr (the pipe holds 64 KB) before the dump: `pg_dump` with many objects.
CHATTY_DUMP = (
    sys.executable,
    "-c",
    "import sys; sys.stderr.write('warning: object ' * 20000);"
    " sys.stderr.flush(); sys.stdout.buffer.write(b'dump-bytes-' * 200000)",
)
FAILING_DUMP = (sys.executable, "-c", "import sys; sys.stderr.write('no connection'); sys.exit(3)")


def make_settings(tmp_path: Path, **values: object) -> Settings:
    base: dict[str, object] = {
        "backup_local_dir": str(tmp_path / "local"),
        "backup_age_public_key": "age1testrecipient",
        "heartbeat_backup_url": SecretStr("https://hb.example/ping"),
        "alert_webhook_url": SecretStr("https://chat.example/hook"),
        "quack_version": "abc1234",
    }
    return Settings(_env_file=None, **{**base, **values})  # type: ignore[call-arg]


class Calls:
    def __init__(self) -> None:
        self.pings: list[str] = []
        self.alerts: list[tuple[Settings, str]] = []

    async def ping(self, url: str) -> None:
        self.pings.append(url)

    async def alert(self, settings: Settings, message: str) -> None:
        self.alerts.append((settings, message))


async def backup(
    tmp_path: Path,
    calls: Calls,
    *,
    dump: tuple[str, ...] = FAKE_DUMP,
    now: datetime | None = None,
    **values: object,
) -> int:
    clock = now or datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    return await run_backup(
        make_settings(tmp_path, **values),
        uploader=DirUploader(tmp_path / "r2"),
        commands=BackupCommands(dump=dump, encrypt=FAKE_AGE),
        ping=calls.ping,
        alert=calls.alert,
        now=lambda: clock,
    )


async def test_backup_survives_a_dump_that_fills_the_stderr_pipe(tmp_path: Path):
    """The stderr of `pg_dump` and `age` was read once, at the end: more than the pipe holds and
    the child stopped on the write, the backup hung."""
    calls = Calls()

    assert await asyncio.wait_for(backup(tmp_path, calls, dump=CHATTY_DUMP), timeout=60) == 0

    [uploaded] = (tmp_path / "r2" / "dumps").glob("*.dump.age")
    assert uploaded.read_bytes() == b"AGE:" + DUMP


async def test_backup_streams_dump_through_encryption_and_keeps_a_local_copy(tmp_path: Path):
    calls = Calls()

    assert await backup(tmp_path, calls) == 0

    uploaded = list((tmp_path / "r2" / "dumps").glob("*.dump.age"))
    assert [p.name for p in uploaded] == ["quack-20261103T120000Z-abc1234.dump.age"]
    assert uploaded[0].read_bytes() == b"AGE:" + DUMP
    local = list((tmp_path / "local").glob("quack-*.dump"))
    assert [p.read_bytes() for p in local] == [DUMP]
    if os.name == "posix":
        assert local[0].stat().st_mode & 0o777 == 0o600
        assert (tmp_path / "local").stat().st_mode & 0o777 == 0o700
    assert calls.pings == ["https://hb.example/ping"]
    assert calls.alerts == []


async def test_backup_keeps_two_last_local_copies(tmp_path: Path):
    calls = Calls()
    start = datetime(2026, 11, 3, 0, 0, tzinfo=UTC)

    for i in range(3):
        assert await backup(tmp_path, calls, now=start + timedelta(hours=6 * i)) == 0

    names = sorted(p.name for p in (tmp_path / "local").glob("quack-*.dump"))
    assert names == ["quack-20261103T060000Z-abc1234.dump", "quack-20261103T120000Z-abc1234.dump"]
    assert len(list((tmp_path / "r2" / "dumps").glob("*.dump.age"))) == 3  # R2 keeps by lifecycle


async def test_failed_dump_alerts_and_leaves_no_copy_and_no_heartbeat(tmp_path: Path):
    calls = Calls()

    assert await backup(tmp_path, calls, dump=FAILING_DUMP) == 1

    assert calls.pings == []
    assert len(calls.alerts) == 1
    _, message = calls.alerts[0]
    assert "CRITICAL" in message and "pg_dump exited with 3" in message
    assert list((tmp_path / "local").glob("quack-*")) == []
    assert list((tmp_path / "r2").rglob("*.dump.age")) == []


async def test_without_age_key_nothing_is_dumped(tmp_path: Path):
    calls = Calls()

    assert await backup(tmp_path, calls, backup_age_public_key="") == 1

    assert "BACKUP_AGE_PUBLIC_KEY" in calls.alerts[0][1]
    assert not (tmp_path / "r2").exists()


async def test_failing_alert_and_heartbeat_do_not_change_the_result(tmp_path: Path):
    class Broken(Calls):
        async def ping(self, url: str) -> None:
            raise RuntimeError("heartbeat down")

        async def alert(self, settings: Settings, message: str) -> None:
            raise RuntimeError("chat down")

    assert await backup(tmp_path, Broken()) == 0
    assert await backup(tmp_path, Broken(), dump=FAILING_DUMP) == 1


def test_cli_accepts_backup_and_backup_now():
    from quack.cli import build_parser

    parser = build_parser()
    assert parser.parse_args(["ops", "backup"]).when is None
    assert parser.parse_args(["ops", "backup", "now"]).when == "now"
    with pytest.raises(SystemExit):
        parser.parse_args(["ops", "backup", "later"])
