"""Settings of the compute service (ТЗ Фазы 4 §6.7, WP24; Т-§21.3, Д-§4.1, Д-§4.2).

Read from the environment once per process (the upper-case field name); no secrets (compute
has none, Т-§19.2). Numbers the documents do not give are named here (ТЗ Ф4 §0.3), not in code.
"""

from __future__ import annotations

import dataclasses
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
    # Д-§4.2: the directory «чистится по возрасту раз в час». Who and what age the documents do
    # not say — Phase 4 decision D-C-133: compute sweeps it itself every hour, an entry older than
    # one sweep interval is left over from a finished job.
    tmp_sweep_interval_s: int = 3600
    tmp_max_age_s: int = 3600
    # WP22.3 п.1, sheet contour search — the documents give no numbers; Phase 4 decision D-C-110:
    # blur kernel, Canny thresholds, polygon precision as a share of the contour perimeter and
    # the least area of the sheet as a share of the photo.
    align_blur_kernel: int = 5
    align_canny_low: int = 75
    align_canny_high: int = 200
    align_contour_epsilon_share: float = 0.02
    align_min_sheet_area_share: float = 0.2
    # WP21.3, a pen line of `render-strokes` in pixels; D-C-110.
    stroke_thickness_px: int = 2

    @classmethod
    def from_env(cls) -> ComputeSettings:
        values: dict[str, object] = {}
        for item in dataclasses.fields(cls):
            raw = os.environ.get(item.name.upper())
            if raw is not None:
                default = getattr(cls, item.name)
                values[item.name] = type(default)(raw)
        return cls(**values)  # pyright: ignore[reportArgumentType]
