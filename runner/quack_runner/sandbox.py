"""Slots and one sandboxed process (documentation/идея-редактор-кода-и-запуск.md, «Слоты и
изоляция запусков»).

A slot is a uid and a directory (`<work_dir>/slot-<n>`, a tmpfs owned by that uid with mode 700
in compose). One process runs per test: the harness script under the slot uid with resource
limits, its input on stdin (never on the command line or in the environment, which other uids
can read in /proc), its result on a separate pipe. After every process the slot is emptied:
every process of the uid is killed and the directory cleared.

Without the sandbox (a laptop, unit tests) the same code runs without the uid switch and the
resource limits; only the timeouts and the output caps apply.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import signal
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from quack_runner.settings import RunnerSettings

HERE = Path(__file__).resolve().parent
PYTHON_HARNESS = HERE / "harness_python.py"
SQL_HARNESS = HERE / "harness_sql.py"
CLEANUP = HERE / "cleanup_slot.py"
POSIX = os.name == "posix"


class SlotsBusy(Exception):
    """No slot became free in `slot_wait_s`."""


@dataclass(frozen=True)
class Slot:
    index: int
    uid: int
    directory: Path


@dataclass(frozen=True)
class Limits:
    wall_s: float
    cpu_s: int | None
    memory_mb: int
    nproc: int
    output_bytes: int
    result_bytes: int
    file_bytes: int
    open_files: int


@dataclass(frozen=True)
class ProcessOutcome:
    result: dict[str, Any] | None
    stdout: str
    stderr: str
    timed_out: bool
    output_exceeded: bool
    returncode: int | None
    wall_ms: int


class SlotPool:
    def __init__(self, settings: RunnerSettings) -> None:
        self._settings = settings
        self._free: asyncio.Queue[Slot] = asyncio.Queue()
        self._temp: tempfile.TemporaryDirectory[str] | None = None

    def start(self) -> None:
        settings = self._settings
        if settings.sandbox:
            root = Path(settings.work_dir)
        else:
            self._temp = tempfile.TemporaryDirectory(prefix="quack-runner-")
            root = Path(self._temp.name)
        for index in range(1, settings.slots + 1):
            directory = root / f"slot-{index}"
            if not settings.sandbox:
                directory.mkdir(parents=True, exist_ok=True)
            self._free.put_nowait(Slot(index, settings.slot_uid_base + index, directory))

    def stop(self) -> None:
        if self._temp is not None:
            self._temp.cleanup()
            self._temp = None

    @contextlib.asynccontextmanager
    async def slot(self) -> AsyncIterator[Slot]:
        try:
            slot = await asyncio.wait_for(self._free.get(), self._settings.slot_wait_s)
        except TimeoutError as exc:
            raise SlotsBusy from exc
        try:
            yield slot
        finally:
            self._free.put_nowait(slot)


def _child_setup(slot: Slot, limits: Limits | None) -> Callable[[], None]:
    """Runs in the child between fork and exec (root → slot uid). `limits` is None for the
    cleanup process: it must start even when the learner's processes filled RLIMIT_NPROC."""
    import resource

    def setup() -> None:
        if limits is not None:
            memory = limits.memory_mb * 1024 * 1024
            if limits.cpu_s is not None:
                resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_s, limits.cpu_s + 1))
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
            resource.setrlimit(resource.RLIMIT_NPROC, (limits.nproc, limits.nproc))
            resource.setrlimit(resource.RLIMIT_FSIZE, (limits.file_bytes, limits.file_bytes))
            resource.setrlimit(resource.RLIMIT_NOFILE, (limits.open_files, limits.open_files))
            resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.setgroups([])
        os.setgid(slot.uid)
        os.setuid(slot.uid)
        # The slot directory is mode 700 of the slot uid: root without DAC_OVERRIDE cannot enter
        # it, so the child enters it only now. `cwd=` of Popen would chdir before this function
        # under the default event loop (uvloop happens to do it after).
        if limits is not None:
            os.chdir(slot.directory)

    return setup


def _env(slot: Slot) -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(slot.directory),
        "TMPDIR": str(slot.directory),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if not POSIX:
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
    return env


async def _read_capped(
    stream: asyncio.StreamReader, cap: int, on_exceeded: Callable[[], None]
) -> tuple[bytes, bool]:
    data = bytearray()
    exceeded = False
    while chunk := await stream.read(65536):
        if not exceeded:
            data.extend(chunk)
            if len(data) > cap:
                del data[cap:]
                exceeded = True
                on_exceeded()
    return bytes(data), exceeded


def _read_fd_capped(fd: int, cap: int) -> bytes:
    data = bytearray()
    with os.fdopen(fd, "rb") as file:
        while chunk := file.read(65536):
            if len(data) <= cap:
                data.extend(chunk)
    return bytes(data)


def _parse_result(raw: bytes, cap: int) -> dict[str, Any] | None:
    if not raw or len(raw) > cap:
        return None
    try:
        result = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


async def run_process(
    settings: RunnerSettings,
    slot: Slot,
    script: Path,
    payload: dict[str, Any],
    limits: Limits,
) -> ProcessOutcome:
    """Run `script` with `payload` on stdin in `slot`; the slot is empty again afterwards."""
    result_fd: int | None = None
    result_path: Path | None = None
    kwargs: dict[str, Any] = {}
    if POSIX:
        read_fd, result_fd = os.pipe()
        payload = {**payload, "result": {"fd": result_fd}}
        kwargs = {"pass_fds": (result_fd,), "start_new_session": True}
        if settings.sandbox:
            kwargs["preexec_fn"] = _child_setup(slot, limits)
    else:
        read_fd = None
        result_path = slot.directory / f".result-{os.urandom(8).hex()}.json"
        payload = {**payload, "result": {"path": str(result_path)}}

    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        settings.python,
        "-S",
        "-s",
        "-B",
        "-P",
        str(script),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        # In the sandbox the child enters the slot directory itself, after the uid switch.
        cwd="/" if settings.sandbox else str(slot.directory),
        env=_env(slot),
        **kwargs,
    )
    if result_fd is not None:
        os.close(result_fd)

    def kill() -> None:
        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
            if POSIX:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()

    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    readers = asyncio.gather(
        _read_capped(proc.stdout, limits.output_bytes, kill),
        _read_capped(proc.stderr, limits.output_bytes, kill),
        asyncio.to_thread(_read_fd_capped, read_fd, limits.result_bytes)
        if read_fd is not None
        else asyncio.sleep(0, result=b""),
    )
    with contextlib.suppress(BrokenPipeError, ConnectionResetError):
        proc.stdin.write(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        await proc.stdin.drain()
        proc.stdin.close()

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), limits.wall_s)
    except TimeoutError:
        timed_out = True
        kill()
        await proc.wait()
    wall_ms = int((time.monotonic() - started) * 1000)

    raw_file = b""
    if result_path is not None:
        with contextlib.suppress(OSError):
            raw_file = result_path.read_bytes()

    # Children the learner started may still hold the pipes open: empty the slot first.
    await clean_slot(settings, slot)
    try:
        (stdout, out_exceeded), (stderr, err_exceeded), raw = await asyncio.wait_for(readers, 5.0)
    except TimeoutError:
        readers.cancel()
        stdout, stderr, raw, out_exceeded, err_exceeded = b"", b"", b"", False, False

    if result_path is not None:
        raw = raw_file

    returncode = proc.returncode
    if POSIX and returncode == -signal.SIGXCPU:
        timed_out = True
    return ProcessOutcome(
        result=_parse_result(raw, limits.result_bytes),
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        output_exceeded=out_exceeded or err_exceeded,
        returncode=returncode,
        wall_ms=wall_ms,
    )


async def clean_slot(settings: RunnerSettings, slot: Slot) -> None:
    if settings.sandbox:
        proc = await asyncio.create_subprocess_exec(
            settings.python,
            "-S",
            "-s",
            "-B",
            "-P",
            str(CLEANUP),
            str(slot.directory),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            cwd="/",
            env=_env(slot),
            preexec_fn=_child_setup(slot, None),
        )
        await proc.wait()
        return
    await asyncio.to_thread(_clear_directory, slot.directory)


def _clear_directory(directory: Path) -> None:
    for entry in directory.iterdir():
        with contextlib.suppress(OSError):
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()


__all__ = [
    "PYTHON_HARNESS",
    "SQL_HARNESS",
    "Limits",
    "ProcessOutcome",
    "Slot",
    "SlotPool",
    "SlotsBusy",
    "clean_slot",
    "run_process",
]
