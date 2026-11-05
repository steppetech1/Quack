"""ТЗ Ф4 WP17.3 (`Т-§14.6`, D-A-203): the pace forecast warns only when it falls short."""

from __future__ import annotations

from quack.config import Settings
from quack.review.today import pace_warning

DAY = 86_400_000
NOW = 100 * DAY
S = Settings()  # window 14, min active days 5, margin 0.8


def test_pace_warning() -> None:
    ready = [NOW - 2 * DAY, NOW - 3 * DAY]  # two topics ready in the window
    # no history: fewer than pace_min_active_days days of activity — no forecast at all
    assert pace_warning(ready, 10, 4, 30, NOW, S) is None
    # 2 topics / 7 active days, 7 of 14 days active, 30 days left → 15 active days → 4.29
    assert pace_warning(ready, 4, 7, 30, NOW, S) is None  # 4.29 ≥ 0.8 × 4
    assert pace_warning(ready, 10, 7, 30, NOW, S) == 4.286  # < 0.8 × 10: warn
    assert pace_warning([NOW - 20 * DAY], 3, 7, 30, NOW, S) == 0.0  # outside the window
    assert pace_warning(ready, 0, 7, 30, NOW, S) is None  # all ready
