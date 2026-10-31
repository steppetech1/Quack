"""Named thresholds of Stream 3 practice and search.

Decision 2: `quack/config.py` is frozen for Phase 2 streams, so the parameters live here and
move into `Settings` at integration. Values from the docs are starting values (Т-§0.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PracticeParams(BaseModel):
    """Search thresholds of stream C (WP1.6). Practice thresholds moved to `Settings`,
    block of stream B (D-C-013)."""

    model_config = ConfigDict(frozen=True)

    # ТЗ §2.3 п.3: `q` has at least 2 characters.
    search_min_query_len: int = 2
    # ТЗ §2.3 п.3, ТЗ §3: `limit: int = 20`.
    search_default_limit: int = 20
    # A13: `limit` is between 1 and 100.
    search_max_limit: int = 100


PARAMS = PracticeParams()
