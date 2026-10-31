"""Named thresholds of Stream 3 practice and search.

Decision 2: `quack/config.py` is frozen for Phase 2 streams, so the parameters live here and
move into `Settings` at integration. Values from the docs are starting values (Т-§0.3).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PracticeParams(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Т-§7.4, decision 1: independence penalty of hint level k is 0.25·k (k = 1..3).
    hint_penalties: tuple[float, float, float] = (0.25, 0.5, 0.75)
    # ТЗ §2.2 п.2: hint_level = min(3, current_max_level + 1).
    max_hint_level: int = 3
    # Decision 8: tested_depth of skill_scores when the verdict comes without the LLM.
    compute_tested_depth: float = 0.5
    # A6: target_difficulty passed to task_gen@1.
    default_target_difficulty: float = 0.5


PARAMS = PracticeParams()
