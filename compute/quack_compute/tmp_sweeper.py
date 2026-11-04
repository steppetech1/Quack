"""Hourly sweep of the temporary directory (Д-§4.2 «чистится по возрасту раз в час»; D-C-133).

Compute is the only writer of `compute_tmp_dir` (`TMPDIR` of the image), so it sweeps it itself.
A top-level entry whose newest file is older than `tmp_max_age_s` is removed. The profile of a
running Chromium (`playwright_*`) is kept while the browser is open: removing it breaks the
renderer (D-C-110 п.6).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path

BROWSER_PROFILE_PREFIX = "playwright"


def _newest_mtime(entry: Path) -> float:
    if not entry.is_dir():
        return entry.stat().st_mtime
    newest = entry.stat().st_mtime
    for path in entry.rglob("*"):
        try:
            newest = max(newest, path.stat().st_mtime)
        except FileNotFoundError:
            continue
    return newest


def sweep_tmp(
    directory: Path, max_age_s: int, *, browser_open: Callable[[], bool], now: float | None = None
) -> list[str]:
    """Remove stale top-level entries; returns their names."""
    if not directory.is_dir():
        return []
    cutoff = (time.time() if now is None else now) - max_age_s
    removed: list[str] = []
    for entry in directory.iterdir():
        if entry.name.startswith(BROWSER_PROFILE_PREFIX) and browser_open():
            continue
        try:
            if _newest_mtime(entry) >= cutoff:
                continue
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        except FileNotFoundError:
            continue
        removed.append(entry.name)
    return removed
