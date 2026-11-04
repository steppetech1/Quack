"""Phase B of `rules@3`: the fold of one skill (ТЗ Ф4 WP4; Т-§7.3, Т-§7.4, Т-§8.1–Т-§8.4).

`fold_skill(evidence, exposures, params)` is pure (Т-§2.5): no current time, the same input
gives the same output. Input — every active evidence and exposure of the skill; output — the
properties of the skill (Т-§3.4.4) and the computed properties of each evidence (weight,
novelty, `delayed`, Т-§8.1). Level 0 (a canvas diagnosis, WP5.1) takes part only in the gap
type and the last practice.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import fsrs

from quack.config import Settings
from quack.graph.projection.types import Props

LEVELS = (1, 2, 3, 4, 5, 6)
_MS_PER_H = 3_600_000
_MS_PER_MIN = 60_000
_NO_GAP = frozenset({"none", "unknown", ""})
_EXPOSURE_PROPS = {
    "explained": "expLastExplainedAtMs",
    "example": "expLastExampleAtMs",
    "method_named": "expLastMethodNamedAtMs",
    "solution_shown": "expLastSolutionShownAtMs",
}


@dataclass(frozen=True)
class FoldEvidence:
    """One active evidence of the skill with the facts of its task (`Т-§3.4.2`)."""

    id: str
    at_ms: int
    seq: int
    level: int
    outcome: str  # correct | incorrect | partial
    tier: int
    verification: str  # code | model
    assistance: str = "none"  # none | hint | shown_solution
    hint_step: int = 0
    derived_kind: str = "direct"  # direct | indirect | transfer
    gap_type: str = "none"
    family_id: str = ""  # "" — not on a task
    task_domain_id: str = ""
    options: int = 0  # k of a choice task; 0 — open answer
    task_max_level: int = 0


@dataclass(frozen=True)
class FoldExposure:
    at_ms: int
    seq: int
    kind: str


@dataclass(frozen=True)
class FoldParams:
    """The parameters of `Т-§7.4`, `Т-§8.2`–`Т-§8.4`; part of `rules@3` (`Т-§2.5`)."""

    tier_weights: Mapping[int, float] = field(
        default_factory=lambda: {1: 1.0, 2: 0.8, 3: 0.4, 4: 0.15}
    )
    hint_step_penalty: float = 0.25
    hot_window_ms: int = 10 * _MS_PER_MIN
    hot_window_factor: float = 0.5
    level_recency_factor: float = 0.6
    proof_min_weight: float = 0.8
    fail_min_weight: float = 0.8
    unstable_min_weight: float = 0.4
    delayed_fail_factor: float = 0.25
    level_implications: Mapping[int, Sequence[int]] = field(
        default_factory=lambda: {3: [1], 4: [3, 1], 5: [1], 6: [3, 1]}
    )
    delay_threshold_ms: int = 20 * _MS_PER_H
    target_retention: float = 0.9
    gap_window: int = 5
    derived_factor: Mapping[str, float] = field(
        default_factory=lambda: {"direct": 1.0, "indirect": 0.5, "transfer": 0.6}
    )

    @classmethod
    def from_settings(cls, s: Settings) -> FoldParams:
        tiers = {int(k.removeprefix("T")): v for k, v in s.evidence_tier_weights.items()}
        return cls(
            tier_weights=tiers,
            hint_step_penalty=s.hint_step_penalty,
            hot_window_ms=s.hot_window_min * _MS_PER_MIN,
            hot_window_factor=s.hot_window_factor,
            level_recency_factor=s.level_recency_factor,
            proof_min_weight=s.proof_min_weight,
            fail_min_weight=s.fail_min_weight,
            unstable_min_weight=s.unstable_min_weight,
            delayed_fail_factor=s.delayed_fail_factor,
            level_implications={int(k): list(v) for k, v in s.level_implications.items()},
            delay_threshold_ms=s.delay_threshold_h * _MS_PER_H,
            target_retention=s.target_retention,
            gap_window=s.gap_window,
            derived_factor={
                "direct": 1.0,
                "indirect": s.derived_indirect_factor,
                "transfer": s.derived_transfer_factor,
            },
        )


@dataclass(frozen=True)
class SkillFold:
    skill: Props
    evidence: dict[str, Props]


def _key(e: FoldEvidence) -> tuple[int, int, str]:
    return (e.at_ms, e.seq, e.id)


# --- computed properties of evidence (Т-§7.4, Т-§5.4, Т-§8.3) --------------------------------


def _novelty(e: FoldEvidence, earlier: Sequence[FoldEvidence], skill_domain: str) -> str:
    if e.family_id and any(p.family_id == e.family_id for p in earlier):
        return "same"
    if (
        e.task_domain_id
        and skill_domain
        and e.task_domain_id != skill_domain
        and not any(p.task_domain_id == e.task_domain_id for p in earlier)
    ):
        return "cross_domain"
    return "new"


def _hot(e: FoldEvidence, exposures: Sequence[FoldExposure], p: FoldParams) -> bool:
    return any(e.at_ms - p.hot_window_ms <= x.at_ms <= e.at_ms for x in exposures)


def _weight(e: FoldEvidence, hot: bool, p: FoldParams) -> float:
    if e.assistance == "shown_solution":
        return 0.0  # a bypass, not understanding (Т-§7.4)
    w = p.tier_weights.get(e.tier, 0.0) * p.derived_factor.get(e.derived_kind, 1.0)
    if e.options > 0:
        w *= 1 - 1 / e.options
    if hot:
        w *= p.hot_window_factor
    if e.hint_step > 0:
        w *= max(0.0, 1 - p.hint_step_penalty * e.hint_step)
    return w


def _delayed(e: FoldEvidence, practice_ms: Sequence[int], p: FoldParams) -> bool:
    before = [t for t in practice_ms if t < e.at_ms]
    return bool(before) and e.at_ms - max(before) > p.delay_threshold_ms


# --- understanding (Т-§7.3, Т-§8.2) ----------------------------------------------------------


@dataclass(frozen=True)
class _Ev:
    e: FoldEvidence
    weight: float
    novelty: str
    delayed: bool
    hot: bool


def _opens(x: _Ev, level: int) -> bool:
    """Т-§7.3 apart from the pair rule: T1/T2, direct or derived from T1/T2, no shown
    solution, not `partial`; levels 4–6 — novelty `new` / `cross_domain`."""
    e = x.e
    return (
        e.level == level
        and e.tier in (1, 2)
        and e.assistance != "shown_solution"
        and e.outcome in ("correct", "incorrect")
        and (level < 4 or x.novelty in ("new", "cross_domain"))
    )


def _paired(x: _Ev, of_level: Sequence[_Ev]) -> bool:
    """A model verdict passes only with a second one of the same outcome: a model verdict on a
    task of another family, or a T1 of code (Т-§7.3)."""
    if x.e.verification != "model":
        return True
    return any(
        y is not x
        and y.e.outcome == x.e.outcome
        and _opens(y, x.e.level)
        and (
            (y.e.verification == "code" and y.e.tier == 1)
            or (y.e.verification == "model" and y.e.family_id != x.e.family_id)
        )
        for y in of_level
    )


@dataclass(frozen=True)
class _Level:
    status: str = "unknown"
    basis: str = "none"
    decisive: str = ""
    success_w: float = 0.0
    fail_w: float = 0.0
    proven_at: int = 0
    failed_at: int = 0


def _own_level(
    level: int, items: Sequence[_Ev], memory: set[str], p: FoldParams
) -> tuple[_Level, bool]:
    """Status of one level by its own evidence; the flag — the level has gate-passing evidence."""
    of_level = [x for x in items if x.e.level == level]
    gating = [x for x in of_level if _opens(x, level) and _paired(x, of_level)]
    if not gating:
        return _Level(), False
    counted = [x for x in gating if x.e.id not in memory]  # memory failures: not in k
    s = f = 0.0
    for x in of_level:
        later = sum(1 for y in counted if _key(y.e) > _key(x.e))
        w = x.weight * p.level_recency_factor**later
        e = x.e
        if x in gating:
            if e.outcome == "correct":
                s += w
            elif e.id in memory:
                f += x.weight * p.delayed_fail_factor
            else:
                f += w
        elif e.outcome == "partial" and e.tier in (1, 2) and e.assistance != "shown_solution":
            s, f = s + w / 2, f + w / 2
        elif e.outcome == "correct" and (
            (e.tier <= 3 and level <= 3) or (e.tier == 4 and level == 1)
        ):
            s += w  # T2 without a pair, T3, T4 — support behind the gate (Т-§7.2)
    successes = [x for x in gating if x.e.outcome == "correct"]
    failures = [x for x in gating if x.e.outcome == "incorrect"]
    real_failures = [x for x in failures if x.e.id not in memory]
    last_s = max((_key(x.e) for x in successes), default=None)
    last_f = max((_key(x.e) for x in real_failures), default=None)
    proven_at = max((x.e.at_ms for x in successes), default=0)
    failed_at = max((x.e.at_ms for x in failures), default=0)
    if (
        successes
        and s >= p.proof_min_weight
        and s >= 2 * f
        and (last_f is None or (last_s is not None and last_s > last_f))
    ):
        status, decisive = "proven", max(successes, key=lambda x: _key(x.e))
    elif (
        real_failures
        and f >= p.fail_min_weight
        and f >= 2 * s
        and (last_s is None or (last_f is not None and last_s < last_f))
    ):
        status, decisive = "failed", max(real_failures, key=lambda x: _key(x.e))
    elif s >= p.unstable_min_weight and f >= p.unstable_min_weight:
        status, decisive = "unstable", max(gating, key=lambda x: _key(x.e))
    else:
        return _Level(success_w=s, fail_w=f, proven_at=proven_at, failed_at=failed_at), True
    return (
        _Level(status, decisive.e.derived_kind, decisive.e.id, s, f, proven_at, failed_at),
        True,
    )


def _levels(items: Sequence[_Ev], memory: set[str], p: FoldParams) -> dict[int, _Level]:
    own = {n: _own_level(n, items, memory, p) for n in LEVELS}
    implied = {
        n
        for m, targets in p.level_implications.items()
        if own.get(int(m), (_Level(), False))[0].status == "proven"
        for n in targets
    }
    result: dict[int, _Level] = {}
    for n in LEVELS:
        state, has_own = own[n]
        if not has_own and n in implied:
            state = _Level("implied", "implied")
        elif state.status == "failed" and n in implied:  # direct beats implication
            state = _Level(
                "unstable",
                state.basis,
                state.decisive,
                state.success_w,
                state.fail_w,
                state.proven_at,
                state.failed_at,
            )
        result[n] = state
    return result


# --- the fold ---------------------------------------------------------------------------------


def _ms(at: datetime) -> int:
    return int(at.timestamp() * 1000)


def _at(ms: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=ms)


def fold_skill(
    evidence: Sequence[FoldEvidence],
    exposures: Sequence[FoldExposure],
    params: FoldParams,
    *,
    skill_domain: str = "",
) -> SkillFold:
    p = params
    ordered = sorted(evidence, key=_key)
    shows = sorted(exposures, key=lambda x: (x.at_ms, x.seq))
    practice = [e.at_ms for e in ordered] + [x.at_ms for x in shows]
    items: list[_Ev] = []
    for e in ordered:
        earlier = [y for y in ordered if _key(y) < _key(e) and (y.at_ms, y.seq) != (e.at_ms, e.seq)]
        hot = _hot(e, shows, p)
        items.append(_Ev(e, _weight(e, hot, p), _novelty(e, earlier, skill_domain),
                         _delayed(e, practice, p), hot))  # fmt: skip

    # Walk in time: memory failures (Т-§8.2 п.3) and FSRS reviews (Т-§8.3) need the status of
    # the level before each evidence.
    memory: set[str] = set()
    scheduler = fsrs.Scheduler(
        desired_retention=p.target_retention,
        learning_steps=(),
        relearning_steps=(),
        enable_fuzzing=False,
    )
    card: fsrs.Card | None = None
    ret_state, reviews, lapses, last_lapse = "none", 0, 0, 0
    consolidation = {"needs": False, "level": 0, "since": 0, "bypass": 0}
    bypassed: set[tuple[int, int]] = set()
    last_memory_failure: dict[int, int] = {}
    groups: dict[tuple[int, int], list[_Ev]] = {}
    for x in items:
        groups.setdefault((x.e.at_ms, x.e.seq), []).append(x)
    done: list[_Ev] = []
    for (at_ms, seq), group in sorted(groups.items()):
        before = _levels(done, memory, p)
        rating: fsrs.Rating | None = None
        for x in group:
            e = x.e
            if e.level == 0:
                continue
            prior = before[e.level].status in ("proven", "implied")
            passes = _opens(x, e.level) and _paired(x, [*done, *group])
            if e.outcome == "incorrect" and passes and x.delayed and prior:
                # After a reminder (an exposure since the last memory failure of the level) a
                # failure is a failure again (`Т-§8.2` п.3).
                reminded = e.level in last_memory_failure and any(
                    last_memory_failure[e.level] < s.at_ms < at_ms for s in shows
                )
                if not reminded:
                    memory.add(e.id)
                    last_memory_failure[e.level] = at_ms
            reviewable = (passes or e.outcome == "partial") and x.delayed and prior and not x.hot
            if card is None and passes and e.outcome == "correct":
                rating = fsrs.Rating.Hard if e.assistance == "hint" else fsrs.Rating.Good
            elif card is not None and reviewable:
                if e.outcome == "incorrect":
                    rating = fsrs.Rating.Again
                elif rating is not fsrs.Rating.Again:
                    hard = e.assistance == "hint" or e.outcome == "partial"
                    rating = fsrs.Rating.Hard if hard else fsrs.Rating.Good
            if e.assistance == "shown_solution" and (at_ms, seq) not in bypassed:
                bypassed.add((at_ms, seq))
                consolidation.update(
                    needs=True,
                    level=max(e.task_max_level, e.level),
                    since=at_ms,
                    bypass=consolidation["bypass"] + 1,
                )
            elif (
                consolidation["needs"]
                and passes
                and e.outcome == "correct"
                and e.level >= consolidation["level"]
            ):
                consolidation.update(needs=False, level=0, since=0)
        if rating is not None:
            first = card is None
            card, _ = scheduler.review_card(card or fsrs.Card(card_id=0), rating, _at(at_ms))
            reviews += 1
            ret_state = "learning" if first else "review"
            if rating is fsrs.Rating.Again:
                lapses, last_lapse = lapses + 1, at_ms
        done.extend(group)

    levels = _levels(items, memory, p)
    skill: Props = {}
    for n, state in levels.items():
        skill |= {
            f"l{n}Status": state.status,
            f"l{n}Basis": state.basis,
            f"l{n}DecisiveEvidenceId": state.decisive,
            f"l{n}SuccessW": round(state.success_w, 6),
            f"l{n}FailW": round(state.fail_w, 6),
            f"l{n}ProvenAtMs": state.proven_at,
            f"l{n}FailedAtMs": state.failed_at,
        }
    skill |= {
        "retState": ret_state,
        "retStability": float(card.stability or 0.0) if card else 0.0,
        "retDifficulty": float(card.difficulty or 0.0) if card else 0.0,
        "retLastRecallAtMs": _ms(card.last_review) if card and card.last_review else 0,
        "retDueAtMs": _ms(card.due) if card else 0,
        "retReviews": reviews,
        "retLapses": lapses,
        "retLastLapseAtMs": last_lapse,
        "expCount": len(shows),
    }
    for kind, prop in _EXPOSURE_PROPS.items():
        skill[prop] = max((x.at_ms for x in shows if x.kind == kind), default=0)
    negatives = [x for x in items if x.e.outcome == "incorrect" and x.e.gap_type not in _NO_GAP]
    votes: dict[str, float] = {}
    for x in negatives[-p.gap_window :]:
        votes[x.e.gap_type] = votes.get(x.e.gap_type, 0.0) + max(x.weight, 1e-9)
    total = sum(votes.values())
    gap = min(votes, key=lambda g: (-votes[g], g)) if votes else "none"
    skill |= {
        "gapType": gap,
        "gapTypeConfidence": round(votes[gap] / total, 6) if votes else 0.0,
        "lastPracticeAtMs": max((e.at_ms for e in ordered), default=0),
        "needsConsolidation": consolidation["needs"],
        "consolidationLevel": consolidation["level"],
        "consolidationSinceMs": consolidation["since"],
        "bypassCount": consolidation["bypass"],
        # The last event the state rests on — from the inputs, so batching does not change it.
        "asOfSeq": max([e.seq for e in ordered] + [x.seq for x in shows], default=0),
    }
    computed = {
        x.e.id: {"weight": round(x.weight, 6), "novelty": x.novelty, "delayed": x.delayed}
        for x in items
    }
    return SkillFold(skill, computed)


def gate_flags(evidence: Sequence[FoldEvidence], novelty: Mapping[str, str]) -> dict[str, bool]:
    """Whether each evidence of one skill passes the gate of its level (`Т-§7.3`, pair rule
    included), with the novelty the fold already wrote — for the profile (WP5.3)."""
    items = [_Ev(e, 0.0, novelty.get(e.id, "new"), False, False) for e in evidence]
    return {
        x.e.id: x.e.level >= 1
        and _opens(x, x.e.level)
        and _paired(x, [y for y in items if y.e.level == x.e.level])
        for x in items
    }
