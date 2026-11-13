"""Т-§19.8: the size limit of uploads, shared by photos and voice segments (WP19, WP21.6)."""

from __future__ import annotations

import pytest

from quack.storage.uploads import UploadRejected, check_upload_size, read_upload

MB = 1024 * 1024


def test_size_within_the_limit_passes() -> None:
    check_upload_size(2 * MB, max_mb=2)


def test_size_over_the_limit_is_413() -> None:
    with pytest.raises(UploadRejected) as caught:
        check_upload_size(2 * MB + 1, max_mb=2)
    assert (caught.value.reason, caught.value.status_code) == ("size", 413)


def test_unknown_size_is_checked_later() -> None:
    check_upload_size(None, max_mb=2)


class _Upload:
    """An `UploadFile` that counts what was asked of it."""

    def __init__(self, total: int, *, known: bool = True) -> None:
        self.size: int | None = total if known else None
        self._left = total
        self.requested: list[int] = []

    async def read(self, size: int = -1, /) -> bytes:
        self.requested.append(size)
        taken = self._left if size < 0 else min(size, self._left)
        self._left -= taken
        return b"\x00" * taken


async def test_a_known_oversize_upload_is_refused_before_a_byte_is_read() -> None:
    """The audit case: `await file.read()` pulled a multi-gigabyte body into memory first."""
    upload = _Upload(3 * MB)
    with pytest.raises(UploadRejected) as caught:
        await read_upload(upload, max_mb=2)
    assert caught.value.status_code == 413
    assert upload.requested == []


async def test_an_unknown_size_is_read_at_most_one_byte_past_the_limit() -> None:
    upload = _Upload(500 * MB, known=False)
    with pytest.raises(UploadRejected):
        await read_upload(upload, max_mb=2)
    assert upload.requested == [2 * MB + 1]


async def test_an_upload_within_the_limit_is_read_whole() -> None:
    upload = _Upload(2 * MB)
    assert len(await read_upload(upload, max_mb=2)) == 2 * MB
