"""`ops backup [now]`: encrypted Postgres dump to R2 and two local copies (Д-§8.1, ТЗ Фазы 4 WP9.5).

`pg_dump -Fc --compress=zstd` is read as a stream. Each chunk goes to a local copy (not encrypted:
there is no private age key on the server, Д-§8.1) and to `age -r <public key>`, whose output is
uploaded to bucket `quack-backups` under `dumps/`. The two last local copies stay in the backup
directory (directory 700, files 600). After a successful run the heartbeat URL is pinged; after
a failed one the alert webhook is called and the exit code is non-zero (Д-§9.3).

`ops backup` (the `backup` container, run by the host timer) and `ops backup now` (before a
migration, `deploy.sh`) are the same operation.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import unquote, urlparse

import aioboto3  # pyright: ignore[reportMissingTypeStubs]
import httpx
from aiobotocore.config import AioConfig  # pyright: ignore[reportMissingTypeStubs]

from quack.config import Settings
from quack.logging import get_logger
from quack.ops.notify import post_alert

log = get_logger(__name__)

DUMP_PREFIX = "dumps/"
LOCAL_COPIES_KEPT = 2  # Д-§8.1: "две последние копии дампа"
CHUNK_BYTES = 1024 * 1024
# S3 multipart parts are at least 5 MiB except the last one.
PART_BYTES = 8 * 1024 * 1024
HTTP_TIMEOUT_S = 10.0


class BackupFailed(Exception):
    """A step of the backup failed; nothing is reported as a successful backup."""


class ObjectUploader(Protocol):
    async def upload(self, key: str, chunks: AsyncIterator[bytes]) -> None: ...


class DirUploader:
    """Writes the object into a directory: the file replacement of R2 for tests and for a local
    stack (`BACKUP_S3_ENDPOINT=file:///path`). MinIO is not used (Т-§20.3, "Отвергнуто")."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def upload(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        target = self._root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        with tmp.open("wb") as out:
            async for chunk in chunks:
                out.write(chunk)
        tmp.replace(target)


class S3Uploader:
    """Multipart upload to the S3 API of R2 with the key limited to `quack-backups` (Д-§7)."""

    def __init__(self, *, endpoint: str, access_key: str, secret_key: str, bucket: str) -> None:
        self._endpoint = endpoint
        self._access_key = access_key
        self._secret_key = secret_key
        self._bucket = bucket

    async def upload(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        session = aioboto3.Session(  # pyright: ignore[reportUnknownMemberType]
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name="auto",  # R2 ignores the region; botocore needs a value
        )
        config = AioConfig(signature_version="s3v4", s3={"addressing_style": "path"})
        context = cast(
            "AbstractAsyncContextManager[Any]",
            session.client("s3", endpoint_url=self._endpoint, config=config),  # pyright: ignore[reportUnknownMemberType]
        )
        async with context as client:
            started = await client.create_multipart_upload(Bucket=self._bucket, Key=key)
            upload_id = started["UploadId"]
            parts: list[dict[str, Any]] = []
            try:
                async for part in _regroup(chunks, PART_BYTES):
                    number = len(parts) + 1
                    sent = await client.upload_part(
                        Bucket=self._bucket,
                        Key=key,
                        UploadId=upload_id,
                        PartNumber=number,
                        Body=part,
                    )
                    parts.append({"ETag": sent["ETag"], "PartNumber": number})
                if not parts:
                    raise BackupFailed("the encrypted dump is empty")
                await client.complete_multipart_upload(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    MultipartUpload={"Parts": parts},
                )
            except BaseException:
                with contextlib.suppress(Exception):
                    await client.abort_multipart_upload(
                        Bucket=self._bucket, Key=key, UploadId=upload_id
                    )
                raise


async def _regroup(chunks: AsyncIterator[bytes], size: int) -> AsyncIterator[bytes]:
    buffer = bytearray()
    async for chunk in chunks:
        buffer += chunk
        while len(buffer) >= size:
            yield bytes(buffer[:size])
            del buffer[:size]
    if buffer:
        yield bytes(buffer)


def create_uploader(settings: Settings) -> ObjectUploader:
    endpoint = settings.backup_s3_endpoint.get_secret_value()
    if endpoint.startswith("file://"):
        return DirUploader(Path(unquote(urlparse(endpoint).path)))
    return S3Uploader(
        endpoint=endpoint,
        access_key=settings.backup_s3_access_key.get_secret_value(),
        secret_key=settings.backup_s3_secret_key.get_secret_value(),
        bucket=settings.backup_bucket,
    )


@dataclass(frozen=True)
class BackupCommands:
    """The two child processes. `{dsn}` and `{recipient}` are replaced; tests substitute both."""

    dump: Sequence[str] = ("pg_dump", "-Fc", "--compress=zstd", "--dbname", "{dsn}")
    encrypt: Sequence[str] = ("age", "-r", "{recipient}")

    def render(self, *, dsn: str, recipient: str) -> tuple[list[str], list[str]]:
        values = {"dsn": dsn, "recipient": recipient}
        return (
            [part.format(**values) for part in self.dump],
            [part.format(**values) for part in self.encrypt],
        )


type Notify = Callable[[str], Awaitable[None]]


async def _ping(url: str) -> None:
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as http:
        (await http.get(url)).raise_for_status()


def _dump_name(now: datetime, version: str) -> str:
    return f"quack-{now.strftime('%Y%m%dT%H%M%SZ')}-{version}.dump"


def _prune_local(directory: Path) -> None:
    copies = sorted(directory.glob("quack-*.dump"))
    for old in copies[:-LOCAL_COPIES_KEPT]:
        old.unlink()
    for leftover in directory.glob("quack-*.dump.part"):
        leftover.unlink()


async def _stream_dump(
    dump_cmd: Sequence[str],
    encrypt_cmd: Sequence[str],
    local_path: Path,
) -> AsyncIterator[bytes]:
    """Encrypted chunks of the dump; the plain chunks are written to `local_path` on the way."""
    dump = await asyncio.create_subprocess_exec(
        *dump_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    encrypt = await asyncio.create_subprocess_exec(
        *encrypt_cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    assert dump.stdout and encrypt.stdin and encrypt.stdout
    fd = os.open(local_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)

    async def feed() -> None:
        try:
            with os.fdopen(fd, "wb") as local:
                while chunk := await dump.stdout.read(CHUNK_BYTES):  # pyright: ignore[reportOptionalMemberAccess]
                    local.write(chunk)
                    encrypt.stdin.write(chunk)  # pyright: ignore[reportOptionalMemberAccess]
                    await encrypt.stdin.drain()  # pyright: ignore[reportOptionalMemberAccess]
        finally:
            encrypt.stdin.close()  # pyright: ignore[reportOptionalMemberAccess]

    feeder = asyncio.create_task(feed())
    try:
        while out := await encrypt.stdout.read(CHUNK_BYTES):
            yield out
        await feeder
        dump_err, encrypt_err = await asyncio.gather(dump.stderr.read(), encrypt.stderr.read())  # pyright: ignore[reportOptionalMemberAccess]
        dump_code, encrypt_code = await asyncio.gather(dump.wait(), encrypt.wait())
        if dump_code != 0:
            raise BackupFailed(
                f"pg_dump exited with {dump_code}: {dump_err.decode(errors='replace')[-500:]}"
            )
        if encrypt_code != 0:
            raise BackupFailed(
                f"age exited with {encrypt_code}: {encrypt_err.decode(errors='replace')[-500:]}"
            )
    finally:
        feeder.cancel()
        for proc in (dump, encrypt):
            if proc.returncode is None:
                proc.kill()
                await proc.wait()


async def run_backup(
    settings: Settings,
    *,
    uploader: ObjectUploader | None = None,
    commands: BackupCommands | None = None,
    ping: Callable[[str], Awaitable[None]] = _ping,
    alert: Callable[[Settings, str], Awaitable[None]] = post_alert,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """One backup. Returns 0 after a successful dump, upload and heartbeat; 1 otherwise."""
    started = now()
    directory = Path(settings.backup_local_dir)
    name = _dump_name(started, settings.quack_version)
    try:
        recipient = settings.backup_age_public_key
        if not recipient:
            raise BackupFailed("BACKUP_AGE_PUBLIC_KEY is not set: an unencrypted dump never leaves")
        await asyncio.to_thread(directory.mkdir, 0o700, True, True)
        dump_cmd, encrypt_cmd = (commands or BackupCommands()).render(
            dsn=settings.postgres_dsn, recipient=recipient
        )
        local_path = directory / name
        try:
            await (uploader or create_uploader(settings)).upload(
                f"{DUMP_PREFIX}{name}.age", _stream_dump(dump_cmd, encrypt_cmd, local_path)
            )
        except BaseException:
            local_path.unlink(missing_ok=True)
            raise
        _prune_local(directory)
    except Exception as exc:
        log.error("backup_failed", error=str(exc), dump=name)
        try:
            await alert(
                settings, f"CRITICAL: Postgres backup failed ({settings.quack_version}): {exc}"
            )
        except Exception as alert_exc:  # the exit code still reports the failure
            log.error("backup_alert_failed", error=str(alert_exc))
        return 1
    log.info("backup_done", dump=name, seconds=(now() - started).total_seconds())
    heartbeat = settings.heartbeat_backup_url.get_secret_value()
    if heartbeat:
        try:
            await ping(heartbeat)
        except Exception as exc:  # a missed ping is raised by the external service (Д-§9.1)
            log.error("backup_heartbeat_failed", error=str(exc))
    return 0
