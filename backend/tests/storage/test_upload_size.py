"""Т-§19.8: the size limit of uploads, shared by photos and voice segments (WP19, WP21.6)."""

from __future__ import annotations

import pytest

from quack.storage.uploads import UploadRejected, check_upload_size

MB = 1024 * 1024


def test_size_within_the_limit_passes() -> None:
    check_upload_size(2 * MB, max_mb=2)


def test_size_over_the_limit_is_413() -> None:
    with pytest.raises(UploadRejected) as caught:
        check_upload_size(2 * MB + 1, max_mb=2)
    assert (caught.value.reason, caught.value.status_code) == ("size", 413)


def test_unknown_size_is_checked_later() -> None:
    check_upload_size(None, max_mb=2)
