"""Settings of the compute service (ТЗ Фазы 4 §6.7, WP24; Т-§21.3, Д-§4.1, Д-§4.2).

Read from the environment once per process; no secrets (compute has none, Т-§19.2).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ComputeSettings:
    # Т-§1.2, Т-§21.3: processes of the pool.
    compute_workers: int = 4
    # Т-§1.2, Д-§4.1: Chromium closes after this idle time.
    chromium_idle_close_s: int = 120
    # Hard timeout of one PDF, image or render job; the documents give none — Phase 4 decision
    # D-C-107 (conservative: a longer wait). Math jobs carry their own timeout in the request.
    compute_task_timeout_s: float = 60.0
    # Д-§4.2: the only place compute writes temporary files.
    compute_tmp_dir: str = "/srv/quack/tmp"

    @classmethod
    def from_env(cls) -> ComputeSettings:
        defaults = cls()
        return cls(
            compute_workers=int(os.environ.get("COMPUTE_WORKERS", defaults.compute_workers)),
            chromium_idle_close_s=int(
                os.environ.get("CHROMIUM_IDLE_CLOSE_S", defaults.chromium_idle_close_s)
            ),
            compute_task_timeout_s=float(
                os.environ.get("COMPUTE_TASK_TIMEOUT_S", defaults.compute_task_timeout_s)
            ),
            compute_tmp_dir=os.environ.get("COMPUTE_TMP_DIR", defaults.compute_tmp_dir),
        )
