"""Seam S10 (ТЗ Фазы 4 WP24): the pebble pool (`T-CM1`) and the endpoint set of the service."""

from __future__ import annotations

import os
import time

import pytest

from quack_compute.app import create_app
from quack_compute.pool import JobTimeout, TaskPool
from quack_compute.settings import ComputeSettings


def _hang(seconds: float) -> int:
    time.sleep(seconds)
    return os.getpid()


def _pid() -> int:
    return os.getpid()


async def test_pebble_timeout_kills() -> None:
    """T-CM1: a hung job is killed by its timeout, the next job is served."""
    pool = TaskPool(workers=1)
    pool.start()
    try:
        first_pid = await pool.run(_pid, timeout_s=10)
        with pytest.raises(JobTimeout):
            await pool.run(_hang, 30.0, timeout_s=0.5)
        started = time.monotonic()
        next_pid = await pool.run(_pid, timeout_s=10)
        assert time.monotonic() - started < 10
        assert next_pid != first_pid  # the hung process was killed and replaced
    finally:
        pool.stop()


async def test_pool_must_be_started() -> None:
    with pytest.raises(RuntimeError):
        await TaskPool(workers=1).run(_pid, timeout_s=1)


def test_wp24_endpoints_are_declared() -> None:
    """WP24: the endpoint set of compute (math endpoints come from stream B, WP11.5)."""
    paths = create_app(ComputeSettings()).openapi()["paths"]
    for path in (
        "/v1/pdf/pages",
        "/v1/image/align",
        "/v1/image/clean-drawing",
        "/v1/image/render-strokes",
        "/v1/render/snapshots",
        "/v1/render/pdf",
    ):
        assert set(paths[path]) == {"post"}, path
        responses = paths[path]["post"]["responses"]
        assert "schema" in responses["200"]["content"]["application/json"], path


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COMPUTE_WORKERS", "3")
    monkeypatch.setenv("CHROMIUM_IDLE_CLOSE_S", "30")
    settings = ComputeSettings.from_env()
    assert (settings.compute_workers, settings.chromium_idle_close_s) == (3, 30)
    assert ComputeSettings().compute_workers == 4  # Т-§21.3
