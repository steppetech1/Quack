"""Д-§4.2: the temporary directory is swept by age (D-C-133)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from quack_compute.tmp_sweeper import sweep_tmp


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def test_stale_entries_go_fresh_and_live_profile_stay(tmp_path: Path) -> None:
    old_file = tmp_path / "page-1.png"
    old_file.write_bytes(b"x")
    _age(old_file, 7200)
    fresh = tmp_path / "job.pdf"
    fresh.write_bytes(b"x")
    old_dir = tmp_path / "unpacked"
    old_dir.mkdir()
    (old_dir / "a").write_bytes(b"x")
    _age(old_dir / "a", 7200)
    _age(old_dir, 7200)
    busy_dir = tmp_path / "half-old"
    busy_dir.mkdir()
    (busy_dir / "old").write_bytes(b"x")
    _age(busy_dir / "old", 7200)
    (busy_dir / "new").write_bytes(b"x")  # a file written now keeps the whole entry
    profile = tmp_path / "playwright_chromiumdev_profile-abc"
    profile.mkdir()
    _age(profile, 7200)

    removed = sweep_tmp(tmp_path, 3600, browser_open=lambda: True)
    assert sorted(removed) == ["page-1.png", "unpacked"]
    assert fresh.exists() and busy_dir.exists() and profile.exists()

    # The browser closed: its stale profile goes too.
    assert sweep_tmp(tmp_path, 3600, browser_open=lambda: False) == [profile.name]


def test_missing_directory_is_nothing(tmp_path: Path) -> None:
    assert sweep_tmp(tmp_path / "absent", 3600, browser_open=lambda: False) == []
