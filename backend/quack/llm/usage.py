"""Usage and dollar budgets of the gateway (ТЗ Ф4 WP10.4, Т-§11.4, D-B-197..D-B-199).

One row of `ops.llm_usage` per attempt of a model call, with the cost by `Settings.llm_prices`.
The budgets read the same table: `session_agent_budget_usd` for the background roles of a
session and `learner_daily_budget_usd` for the background work of a learner. The teacher's
turns are counted but never limited. SQL is here, parameters are `%(name)s` (Т-§1.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from quack.config import Settings
from quack.db import Db
from quack.llm.access import access_mode, budgets_for, is_meta_judge, pool_remaining
from quack.llm.errors import LLMBudgetExceededError
from quack.logging import get_logger

log = get_logger(__name__)

# Extraction and the analyst are not limited by the session budget (Т-§11.4): without them no
# memory is written. The same set as `quack.jobs.queue_p4.budget_roles` (that module is A's;
# the queue reads this table, so the two must agree).
_ALWAYS_EXEMPT = frozenset({"extractor", "analyst", "analyst_deep"})

_warned_models: set[str] = set()


@dataclass(frozen=True)
class UsageRow:
    learner_id: UUID | None
    session_id: UUID | None
    role: str
    model: str
    prompt_version: str | None
    effort: str | None
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost_usd: float
    incomplete: bool
    retried: bool
    latency_ms: int
    status: str


def call_cost_usd(
    settings: Settings,
    model: str,
    *,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    audio_ms: int = 0,
    images: int = 0,
) -> float:
    """Dollars of one call by the price table. `input_tokens` includes the cached ones and
    `output_tokens` includes the reasoning ones (Responses API usage). A model without a price
    costs 0 and the call goes to the log `llm_prices_missing` (once per model per process)."""
    price = settings.llm_prices.get(model)
    if price is None:
        if model not in _warned_models:
            _warned_models.add(model)
            log.warning("llm_prices_missing", model=model)
        return 0.0
    cached = min(cached_tokens, input_tokens)
    cost = (
        (input_tokens - cached) * price.input
        + cached * price.cached_input
        + output_tokens * price.output
    ) / 1_000_000
    return cost + audio_ms / 60_000 * price.per_minute + images * price.per_image


_INSERT = (
    "INSERT INTO ops.llm_usage (learner_id, session_id, role, model, prompt_version, effort, "
    "input_tokens, cached_input_tokens, output_tokens, reasoning_tokens, cost_usd, incomplete, "
    "retried, latency_ms, status) VALUES (%(learner_id)s, %(session_id)s, %(role)s, %(model)s, "
    "%(prompt_version)s, %(effort)s, %(input_tokens)s, %(cached_input_tokens)s, "
    "%(output_tokens)s, %(reasoning_tokens)s, %(cost_usd)s, %(incomplete)s, %(retried)s, "
    "%(latency_ms)s, %(status)s)"
)

_SESSION_SPEND = (
    "SELECT COALESCE(sum(cost_usd), 0)::float8 AS spent FROM ops.llm_usage "
    "WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
    "AND role = ANY(%(roles)s::text[])"
)

_DAILY_SPEND = (
    "SELECT COALESCE(sum(cost_usd), 0)::float8 AS spent FROM ops.llm_usage "
    "WHERE learner_id = %(learner_id)s AND role = ANY(%(roles)s::text[]) "
    "AND created_at >= date_trunc('day', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'"
)


async def insert_usage(db: Db, learner_id: UUID | None, row: UsageRow) -> None:
    await db.execute(_INSERT, row.__dict__ | {"learner_id": learner_id})


def background_roles(settings: Settings) -> list[str]:
    """Roles of the background classes P2 and P3 (Т-§11.3): they count against the daily
    budget of the learner."""
    return sorted(r for r, c in settings.llm_roles.items() if c.priority_class in ("P2", "P3"))


def limited_roles(settings: Settings) -> list[str]:
    """Background roles the budgets stop: P2/P3 minus the exempt ones (`extractor`, the analyst
    and `session_budget_exempt_roles`)."""
    exempt = set(settings.session_budget_exempt_roles) | _ALWAYS_EXEMPT
    return [r for r in background_roles(settings) if r not in exempt]


async def check_usd_budgets(
    db: Db, settings: Settings, role: str, learner_id: UUID, session_id: UUID | None
) -> None:
    """Raise `LLMBudgetExceededError` before a call: `pool` for every role of a learner who is
    not a judge while the pool is in `judges_only` (D-B-211); `session` or `daily` (unit `usd`)
    for a limited background role, with the budgets of the pool mode. Without prices every cost
    is 0, so nothing can trigger (§2.3 row 9) and the queries are skipped."""
    if not settings.llm_prices:
        return
    mode = await access_mode(db, settings)
    if mode == "judges_only" and not await is_meta_judge(db, settings, learner_id):
        remaining = await pool_remaining(db, settings) or 0.0
        raise LLMBudgetExceededError(
            "pool", remaining, settings.llm_pool_judges_only_remaining_usd, "usd"
        )
    if role not in limited_roles(settings):
        return
    budgets = budgets_for(settings, mode)
    if session_id is not None:
        row = await db.fetch_one(
            _SESSION_SPEND,
            {
                "learner_id": learner_id,
                "session_id": session_id,
                "roles": limited_roles(settings),
            },
        )
        spent = float(row["spent"]) if row else 0.0
        if spent > budgets.session_usd:
            raise LLMBudgetExceededError("session", spent, budgets.session_usd, "usd")
    row = await db.fetch_one(
        _DAILY_SPEND, {"learner_id": learner_id, "roles": background_roles(settings)}
    )
    spent = float(row["spent"]) if row else 0.0
    if spent >= budgets.daily_usd:
        raise LLMBudgetExceededError("daily", spent, budgets.daily_usd, "usd")
