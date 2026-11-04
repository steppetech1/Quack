"""Access to the models by what is left of the money pool (owner decision D-B-211).

- `normal` — the pool is full: generous per-learner budgets (`session_agent_budget_usd`,
  `learner_daily_budget_usd`);
- `tight` — the remainder is at or under `llm_pool_tight_remaining_usd`: the budgets of
  Т-§11.4 (`tight_*`);
- `judges_only` — the remainder is at or under `llm_pool_judges_only_remaining_usd`: only a
  learner who said «I am a Meta judge» (`preference.set {key: meta_judge, value: true}`, one
  button, no other check) calls the models; the others get `LLMBudgetExceededError("pool")`.

The remainder is `llm_pool_budget_usd` minus every cost in `ops.llm_usage` — one sum over all
learners, cached in the process for `llm_pool_cache_s`. Without a pool (0) the mode is always
`normal`; without prices every cost is 0 and the pool never runs low (§2.3 row 9).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from quack.config import Settings
from quack.db import Db

type AccessMode = Literal["normal", "tight", "judges_only"]

# D-B-211: the pool is one sum over every learner — an operational total, not learner data.
_POOL_SPENT = "SELECT COALESCE(sum(cost_usd), 0)::float8 AS spent FROM ops.llm_usage"

_JUDGE = (
    "SELECT payload -> 'value' AS value FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'preference.set' AND payload ->> 'key' = %(key)s ORDER BY seq DESC LIMIT 1"
)

_cache: tuple[float, float] | None = None  # (monotonic time of the read, spent)


@dataclass(frozen=True, slots=True)
class Budgets:
    session_usd: float
    daily_usd: float


def reset_pool_cache() -> None:
    global _cache
    _cache = None


async def pool_spent(db: Db, settings: Settings) -> float:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < settings.llm_pool_cache_s:
        return _cache[1]
    row = await db.fetch_one(_POOL_SPENT, {})
    spent = float(row["spent"]) if row else 0.0
    _cache = (now, spent)
    return spent


def mode_for_remaining(settings: Settings, remaining: float) -> AccessMode:
    if remaining <= settings.llm_pool_judges_only_remaining_usd:
        return "judges_only"
    if remaining <= settings.llm_pool_tight_remaining_usd:
        return "tight"
    return "normal"


async def pool_remaining(db: Db, settings: Settings) -> float | None:
    """Dollars left in the pool; `None` without a pool."""
    if settings.llm_pool_budget_usd <= 0:
        return None
    return settings.llm_pool_budget_usd - await pool_spent(db, settings)


async def access_mode(db: Db, settings: Settings) -> AccessMode:
    remaining = await pool_remaining(db, settings)
    return "normal" if remaining is None else mode_for_remaining(settings, remaining)


def budgets_for(settings: Settings, mode: AccessMode) -> Budgets:
    if mode == "normal":
        return Budgets(settings.session_agent_budget_usd, settings.learner_daily_budget_usd)
    return Budgets(settings.tight_session_agent_budget_usd, settings.tight_learner_daily_budget_usd)


async def is_meta_judge(db: Db, settings: Settings, learner_id: UUID) -> bool:
    """The latest `preference.set` of `meta_judge_preference_key` is `true`."""
    row = await db.fetch_one(
        _JUDGE, {"learner_id": learner_id, "key": settings.meta_judge_preference_key}
    )
    return row is not None and row["value"] is True


async def may_call_models(db: Db, settings: Settings, learner_id: UUID) -> bool:
    """`False` only in `judges_only` for a learner who is not a judge."""
    if await access_mode(db, settings) != "judges_only":
        return True
    return await is_meta_judge(db, settings, learner_id)
