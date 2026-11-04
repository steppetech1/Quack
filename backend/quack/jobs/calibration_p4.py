"""The periodic job `calibration.weekly` (ТЗ Ф4 WP7.5; Т-§18.2, Т-§18.5; D-A-219…D-A-221).

Once a week it measures how well the state predicts the next Т1 attempt and **proposes** new
values of the probabilities `p_*`. The proposal goes to the log (`calibration_proposal`); the job
changes nothing: not `Settings`, not the database, no event (Т-§18.5: a person applies it as
`rules@N+1` and a rebuild; the weights never change by themselves).

- The window is the ISO week before the period of the job (D-A-219): the week is over, the
  result of a retry is the same.
- Before each Т1 attempt (a code verdict of a marked task) the state at that moment —
  `GraphReader.state_at(answer_seq − 1)` — gives the probability by the rule of Т-§18.2; the
  outcome is the verdict. Every (skill, level) the task tests is one observation of one rule.
- Per rule: the Brier score, the calibration curve (buckets of the predicted probability) and the
  proposed value. A rule predicts `p × R` (proven direct: `p_proven`; implied or transfer:
  `p_derived`) or `p` alone; the proposal is the value of `p` that makes the predictions of the
  rule add up to the observed successes: `Σ outcome / Σ R` (`R = 1` for a constant rule), kept
  in [0, 1]. Under `calibration_min_reports` observations the rule gets no proposal (law 8).
- The weights of the evidence kinds (D-A-245): a kind is a tier T1–T4 (Т-§7.2 п.5), the
  calibrated numbers are `Settings.fold.tier_base_weights` (Т-§7.4); the multipliers of §7.4
  (hint, «burning» window, guess) are not calibrated. The unit is an observation above (attempt ×
  tested level). For every tier the active evidence of that tier on the skill and level before the
  answer gives `q`, the share of `correct`; `x = q − 0.5` against `y' = outcome − 0.5`. The
  strength of the tier is the least-squares slope through zero `w* = Σxy' / Σx²` (the trust with
  which the tier moves the expectation 0.5; it minimises the Brier score of `0.5 + w·x`) with the
  standard error `se`. T1 is the scale (its weight is 1.0): `ρ = w*_T / w*_T1`. A tier T2–T4
  gets a **lowered** weight only when it has `min_units` units, T1 is positive with the one-sided
  95% confidence (`z`) and the upper bound of `ρ` is below the current weight («systematically
  fails to confirm», Т-§7.11): `max(ρ, current × (1 − max_step), floor)`. A raise is never
  proposed (`above_current` in the report); after that the order of tiers is kept (reliability
  falls with the tier, Т-§7.2). The numbers of `WeightRule` are starting values.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import Any
from uuid import UUID

from quack.config import Settings
from quack.db import Db
from quack.graph.calibration_read import EvidenceReader, read_tier_evidence, unit_evidence
from quack.graph.read import GraphReader, LevelFact, SkillFact, create_graph_reader
from quack.jobs.runner import JobContext, register_job_handler
from quack.jobs.types import ClaimedJob
from quack.logging import get_logger

log = get_logger(__name__)

CURVE_DIGITS = 1
RULES: tuple[str, ...] = ("p_proven", "p_derived", "p_unstable", "p_failed", "p_unknown")

# T1 attempts of every learner in the window: a code verdict that is right or wrong, the answer
# and the levels its task tests (the same attempt as the metric `next_attempt_brier`).
_ATTEMPTS = (
    "SELECT v.learner_id, a.seq AS answer_seq, v.payload->>'outcome' AS outcome, "
    "t.payload->'markup'->'tests' AS tests "
    "FROM core.events v "
    "JOIN core.events a ON a.learner_id = v.learner_id AND a.type = 'answer.submitted' "
    "AND a.seq = (v.payload->>'answer_seq')::bigint "
    "JOIN core.events t ON t.learner_id = v.learner_id AND t.type = 'task.created' "
    "AND t.payload->>'task_id' = a.payload->>'task_id' "
    "WHERE v.type = 'verdict.recorded' AND v.occurred_at >= %(since)s "
    "AND v.occurred_at < %(until)s AND v.payload->>'verification' = 'code' "
    "AND v.payload->>'outcome' IN ('correct', 'incorrect') "
    "ORDER BY v.occurred_at, v.learner_id, v.seq"
)


@dataclass(frozen=True)
class WeightRule:
    """The rule that proposes new base weights of the tiers (D-A-245); starting values."""

    min_units: int = 30  # units per tier, and of T1, before a proposal
    z: float = 1.645  # one-sided 95%
    max_step: float = 0.5  # a weight falls by at most this share per proposal
    floor: float = 0.05  # and never below


WEIGHT_RULE = WeightRule()
TIERS: tuple[int, ...] = (1, 2, 3, 4)


@dataclass
class TierSums:
    """Sums of one tier over the units with `x ≠ 0` (`x = q − 0.5`, `y' = outcome − 0.5`)."""

    units: int = 0
    ties: int = 0
    sxx: float = 0.0
    sxy: float = 0.0
    syy: float = 0.0

    def add(self, share_correct: float, correct: bool) -> None:
        x, y = share_correct - 0.5, (0.5 if correct else -0.5)
        if x == 0:
            self.ties += 1
            return
        self.units += 1
        self.sxx += x * x
        self.sxy += x * y
        self.syy += y * y


@dataclass
class _Rule:
    """The observations of one rule: predicted probability, `R` (1 for a constant), outcome."""

    predicted: list[float] = field(default_factory=list)
    recall: list[float] = field(default_factory=list)
    outcome: list[float] = field(default_factory=list)


def rule_of(skill: SkillFact | None, level: int) -> tuple[str, float | None]:
    """The rule of Т-§18.2 for the state of one level and its `R`; `R` is `None` when the rule
    needs the recall probability and the state does not know it, and 1.0 for a constant rule."""
    fact: LevelFact | None = (
        next((f for f in skill.levels if f.level == level), None) if skill else None
    )
    if fact is None or fact.status == "not_tested":
        return "p_unknown", 1.0
    if fact.status in {"unstable", "failed"}:
        return f"p_{fact.status}", 1.0
    recall = None if skill is None else skill.recall_probability
    return (
        "p_proven" if fact.status == "proven" and fact.basis == "direct" else "p_derived"
    ), recall


def week_before(period: str) -> tuple[datetime, datetime]:
    """The ISO week before `period` (`2026-45`): Monday 00:00 UTC to the next Monday."""
    year, week = period.split("-")
    start = datetime.fromisocalendar(int(year), int(week), 1).replace(tzinfo=UTC)
    return start - timedelta(days=7), start


async def calibration_proposal(
    db: Db,
    settings: Settings,
    reader: Callable[[UUID], GraphReader],
    evidence: EvidenceReader,
    since: datetime,
    until: datetime,
    weight_rule: WeightRule = WEIGHT_RULE,
) -> dict[str, Any]:
    """Brier score, calibration curve and the proposed `p_*` per rule, and the proposed tier
    weights, over `[since, until)`. Reads only; JSON-ready."""
    rows = await db.fetch_all(_ATTEMPTS, {"since": since, "until": until})
    rows = rows[-settings.metrics_brier_max_attempts :]
    observed: dict[str, _Rule] = {name: _Rule() for name in RULES}
    sums = {tier: TierSums() for tier in TIERS}
    skipped = 0
    units_of: list[list[tuple[UUID, int]]] = []
    skills_of: dict[UUID, set[UUID]] = defaultdict(set)
    for row in rows:
        units = list(
            dict.fromkeys(
                (UUID(str(t["skill_id"])), int(t["level"]))
                for t in row["tests"] or []
                if t.get("skill_id") is not None
            )
        )
        units_of.append(units)
        skills_of[row["learner_id"]].update(skill for skill, _ in units)
    # One read of the evidence per learner (all the skills of the window).
    found = {
        learner_id: await evidence(learner_id, sorted(skill_ids, key=str))
        for learner_id, skill_ids in skills_of.items()
    }
    for row, units in zip(rows, units_of, strict=True):
        if not units:
            continue
        answer_seq = int(row["answer_seq"])
        state = await reader(row["learner_id"]).state_at(
            row["learner_id"], answer_seq - 1, [skill for skill, _ in units]
        )
        by_id = {s.skill_id: s for s in state.skills}
        correct = row["outcome"] == "correct"
        for skill_id, level in units:
            for tier in TIERS:
                of_tier = unit_evidence(found[row["learner_id"]], skill_id, level, tier, answer_seq)
                if of_tier:
                    share = sum(e.outcome == "correct" for e in of_tier) / len(of_tier)
                    sums[tier].add(share, correct)
            name, recall = rule_of(by_id.get(skill_id), level)
            if recall is None:
                skipped += 1
                continue
            bucket = observed[name]
            bucket.predicted.append(getattr(settings, name) * recall)
            bucket.recall.append(recall)
            bucket.outcome.append(1.0 if correct else 0.0)
    every = [
        pair
        for rule in observed.values()
        for pair in zip(rule.predicted, rule.outcome, strict=True)
    ]
    weights, reason = calibrate_weights(sums, settings.fold.tier_base_weights, weight_rule)
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "attempts": len(rows),
        "observations": len(every),
        "skipped_unknown_recall": skipped,
        "brier": _brier(every),
        "rules": {name: _rule_report(settings, name, rule) for name, rule in observed.items()},
        "evidence_weights": weights,
        "evidence_weights_reason": reason,
    }


def _strength(sums: TierSums) -> tuple[float, float] | None:
    """`(w*, se)` of one tier; `None` under two units or without spread of `x`."""
    if sums.units < 2 or sums.sxx <= 0:
        return None
    strength = sums.sxy / sums.sxx
    rss = max(0.0, sums.syy - sums.sxy * sums.sxy / sums.sxx)
    return strength, math.sqrt(rss / (sums.units - 1) / sums.sxx)


def calibrate_weights(
    sums: Mapping[int, TierSums], current: Sequence[float], rule: WeightRule
) -> tuple[dict[str, Any], str | None]:
    """The tier-weight report and its reason (D-A-245): `(evidence_weights, reason)`. `reason` is
    set when the scale (T1) is undefined, not positive with the confidence, or has too few units."""
    fit = {tier: _strength(sums[tier]) for tier in TIERS}
    ref = fit[1]
    reason: str | None = None
    if ref is None or sums[1].units < rule.min_units:
        reason = "the reference tier T1 has too few units or no spread (D-A-245)"
    elif ref[0] - rule.z * ref[1] <= 0:
        reason = "the strength of the reference tier T1 is not positive with confidence (D-A-245)"
    tiers: dict[str, dict[str, Any]] = {}
    proposed: dict[int, float | None] = {1: None}
    for tier in TIERS:
        found = fit[tier]
        relative = found[0] / ref[0] if found and ref and ref[0] > 0 else None
        direction, new = "insufficient", None
        if tier == 1:
            direction = "reference"
        elif reason is None and found and ref and sums[tier].units >= rule.min_units:
            cur = current[tier - 1]
            low_ref, high_ref = ref[0] - rule.z * ref[1], ref[0] + rule.z * ref[1]
            if (found[0] + rule.z * found[1]) / low_ref < cur:
                direction = "below_current"
                new = min(cur, max(found[0] / ref[0], cur * (1 - rule.max_step), rule.floor))
                if new >= cur:
                    direction, new = "keep", None
            elif (found[0] - rule.z * found[1]) / high_ref > cur:
                direction = "above_current"
            else:
                direction = "keep"
        proposed[tier] = new
        tiers[f"T{tier}"] = {
            "units": sums[tier].units,
            "ties": sums[tier].ties,
            "strength": found[0] if found else None,
            "se": found[1] if found else None,
            "relative": relative,
            "direction": direction,
            "proposed": new,
            "capped_by_order": False,
        }
    # Reliability falls with the tier (Т-§7.2): a tier never weighs more than the one above it.
    for tier in (2, 3, 4):
        above = proposed[tier - 1]
        ceiling = current[tier - 2] if above is None else above
        own = proposed[tier]
        value = current[tier - 1] if own is None else own
        if value > ceiling:
            proposed[tier] = tiers[f"T{tier}"]["proposed"] = ceiling
            tiers[f"T{tier}"]["capped_by_order"] = True
    return {
        "current": {f"T{t}": current[t - 1] for t in TIERS},
        "reference_strength": ref[0] if ref else None,
        "tiers": tiers,
    }, reason


def _brier(pairs: list[tuple[float, float]]) -> float | None:
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs) if pairs else None


def _rule_report(settings: Settings, name: str, rule: _Rule) -> dict[str, Any]:
    count = len(rule.outcome)
    curve: dict[float, list[float]] = defaultdict(list)
    for p, o in zip(rule.predicted, rule.outcome, strict=True):
        curve[round(p, CURVE_DIGITS)].append(o)
    proposed = None
    if count >= max(1, settings.calibration_min_reports) and sum(rule.recall) > 0:
        proposed = min(1.0, max(0.0, sum(rule.outcome) / sum(rule.recall)))
    return {
        "current": getattr(settings, name),
        "observations": count,
        "brier": _brier(list(zip(rule.predicted, rule.outcome, strict=True))),
        "observed_rate": sum(rule.outcome) / count if count else None,
        "calibration": [
            {"predicted": p, "observed": sum(o) / len(o), "of": len(o)}
            for p, o in sorted(curve.items())
        ],
        "proposed": proposed,
    }


@register_job_handler("calibration.weekly")
async def handle_calibration_weekly(job: ClaimedJob, ctx: JobContext) -> None:
    period = str(job.payload.get("period", ""))
    since, until = week_before(period)

    def reader(learner_id: UUID) -> GraphReader:
        return create_graph_reader(ctx.neo4j, ctx.settings, learner_id=learner_id, db=ctx.db)

    proposal = await calibration_proposal(
        ctx.db, ctx.settings, reader, partial(read_tier_evidence, ctx.neo4j), since, until
    )
    log.info("calibration_proposal", period=period, **proposal)
