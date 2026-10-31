"""Named thresholds of Stream 3 practice and search.

Decision 2: `quack/config.py` is frozen for Phase 2 streams, so the parameters live here and
move into `Settings` at integration. Values from the docs are starting values (Т-§0.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PracticeParams(BaseModel):
    """Empty after the move: search thresholds are in `Settings` (stream C), practice
    thresholds in the block of stream B (D-C-013). Removal of the module is up to stream C."""

    model_config = ConfigDict(frozen=True)


PARAMS = PracticeParams()
