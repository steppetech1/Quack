"""Phase B: pure folds of skills and misconceptions (Т-§7–9).

No I/O and no current time: all times come from traces (event.occurred_at), §2.5.
Times are int64 milliseconds; 0 means "never" (Т-§3.4.4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from quack.config import FoldParams
from quack.graph.types import Props

type EvidenceKind = Literal["skill", "manifestation"]
type MasteryStatus = Literal["unknown", "exposed", "learning", "mastered", "struggling"]
type MisconceptionStatus = Literal[
    "latent", "suspected", "confirmed", "watching", "fixed", "dismissed", "resolved"
]

MS_PER_DAY = 86_400_000
MS_PER_HOUR = 3_600_000

# FSRS forgetting curve: R(t) = (1 + FACTOR * t / S) ** DECAY, so that R(S) = 0.9.
FSRS_DECAY = -0.5
FSRS_FACTOR = 19.0 / 81.0


@dataclass(frozen=True)
class EvidenceTrace:
    id: str
    seq: int
    at_ms: int
    kind: EvidenceKind
    tier: int  # 1..4, assigned by code from source type and check class (Т-§7.2)
    source_type: str  # 'task' | 'diagnostic' | 'extractor'
    score: float  # 0..1; for extractor observations: 1 positive, 0 negative, 0.5 neutral
    independence: float = 1.0
    tested_depth: float = 0.5
    strength: float = 1.0
    polarity_weight: float = 1.0
    direction: str = "positive"
    family_id: str = "unknown"
    superseded: bool = False


@dataclass(frozen=True)
class ExposureTrace:
    id: str
    seq: int
    at_ms: int
    depth: float


@dataclass(frozen=True)
class SkillPrior:
    mastery: float = 0.0
    confidence: float = 0.0


@dataclass(frozen=True)
class SkillFoldResult:
    p_mastery: float
    confidence: float
    evidence_count: int
    distinct_families: int
    decay_rate: float
    stability: float  # days
    last_practiced_at: int
    last_reviewed_at: int
    next_review_at: int
    mastery_status: MasteryStatus
    exposure_count: int
    last_exposed_at: int

    def to_props(self) -> Props:
        return {
            "pMastery": self.p_mastery,
            "confidence": self.confidence,
            "evidenceCount": self.evidence_count,
            "distinctFamilies": self.distinct_families,
            "decayRate": self.decay_rate,
            "stability": self.stability,
            "lastPracticedAt": self.last_practiced_at,
            "lastReviewedAt": self.last_reviewed_at,
            "nextReviewAt": self.next_review_at,
            "masteryStatus": self.mastery_status,
            "exposureCount": self.exposure_count,
            "lastExposedAt": self.last_exposed_at,
        }


@dataclass(frozen=True)
class MisconceptionFoldResult:
    status: MisconceptionStatus
    occurrences: int
    first_seen_at: int
    last_seen_at: int
    watch_since_at: int
    fixed_at: int
    resist_count: int

    def to_props(self) -> Props:
        return {
            "status": self.status,
            "occurrences": self.occurrences,
            "firstSeenAt": self.first_seen_at,
            "lastSeenAt": self.last_seen_at,
            "watchSinceAt": self.watch_since_at,
            "fixedAt": self.fixed_at,
            "resistCount": self.resist_count,
        }


def retrievability(stability_days: float, elapsed_ms: int) -> float:
    """Forgetting curve R(t) = (1 + F·t/S)^D with R(S) = 0.9 (FSRS curve, Т-§8.3)."""
    if elapsed_ms <= 0:
        return 1.0
    if stability_days <= 0:
        return 0.0
    days = elapsed_ms / MS_PER_DAY
    return (1.0 + FSRS_FACTOR * days / stability_days) ** FSRS_DECAY


def review_interval_ms(stability_days: float, target_retention: float) -> int:
    """Time after which R falls to `target_retention`."""
    if stability_days <= 0:
        return 0
    days = stability_days / FSRS_FACTOR * (target_retention ** (1.0 / FSRS_DECAY) - 1.0)
    return round(days * MS_PER_DAY)


def effective_mastery(
    p_mastery: float, stability_days: float, last_reviewed_at: int, as_of_ms: int
) -> float:
    """pMastery decayed by retrievability since the last gated success (read-time)."""
    if last_reviewed_at <= 0 or stability_days <= 0:
        return p_mastery
    return p_mastery * retrievability(stability_days, as_of_ms - last_reviewed_at)


def skill_is_rusty(fold: SkillFoldResult, as_of_ms: int) -> bool:
    return fold.mastery_status == "mastered" and 0 < fold.next_review_at < as_of_ms


def _order(trace: EvidenceTrace | ExposureTrace) -> tuple[int, int, str]:
    return (trace.at_ms, trace.seq, trace.id)


def _is_gated(e: EvidenceTrace) -> bool:
    """Only T1/T2 evidence passes the gate (Т-§7.3)."""
    return e.tier in (1, 2)


def _weight(e: EvidenceTrace, params: FoldParams) -> float:
    """Т-§7.4: base weight of the tier × independence × tested-depth factor."""
    base = params.tier_base_weights[e.tier - 1]
    depth = params.depth_weight_floor + (1.0 - params.depth_weight_floor) * e.tested_depth
    return base * e.independence * depth


def recompute_skill_fold(
    evidence: Sequence[EvidenceTrace],
    exposures: Sequence[ExposureTrace],
    prior: SkillPrior,
    params: FoldParams,
) -> SkillFoldResult:
    ordered = sorted((e for e in evidence if not e.superseded and e.kind == "skill"), key=_order)
    exps = sorted(exposures, key=_order)
    gated = [e for e in ordered if _is_gated(e)]
    threshold = params.success_score_threshold

    # Understanding (Т-§8.2): w_eff = w · λ^k; S and F from decayed, confidence from raw weights.
    s_sum = f_sum = w_sum = 0.0
    n = len(gated)
    for i, e in enumerate(gated):
        w = _weight(e, params)
        w_eff = w * params.level_recency_factor ** (n - 1 - i)
        s_sum += w_eff * e.score
        f_sum += w_eff * (1.0 - e.score)
        w_sum += w
    families = {e.family_id for e in gated if e.score >= threshold and e.family_id != "unknown"}

    if gated:
        ratio = s_sum / (s_sum + f_sum) if s_sum + f_sum > 0 else 0.0
        confidence = w_sum / (w_sum + params.confidence_half_weight)
        p_mastery = ratio * confidence
        if len(families) < params.min_distinct_families:
            p_mastery = min(p_mastery, params.single_family_mastery_ceiling)
    else:
        # Without gated practice only capped contributions exist (Т-§7.2, §7.3). Negative
        # model observations never lower the state (law 4).
        exposure_part = min(
            params.exposure_mastery_ceiling,
            sum(params.exposure_mastery_gain * x.depth for x in exps),
        )
        ungated = [e for e in ordered if not _is_gated(e)]
        positive = sum(
            params.ungated_evidence_gain
            * params.tier_base_weights[e.tier - 1]
            * e.strength
            * e.polarity_weight
            * e.independence
            for e in ungated
            if e.direction == "positive"
        )
        p_ungated = min(params.ungated_mastery_ceiling, exposure_part + positive)
        prior_part = min(params.prior_mastery_ceiling, prior.mastery * prior.confidence)
        p_mastery = max(p_ungated, prior_part)
        w_ungated = sum(params.tier_base_weights[e.tier - 1] * e.strength for e in ungated)
        ungated_conf = 0.0
        if w_ungated > 0:
            ungated_conf = min(
                params.ungated_mastery_ceiling,
                w_ungated / (w_ungated + params.confidence_half_weight),
            )
        confidence = max(ungated_conf, min(params.prior_mastery_ceiling, prior.confidence))

    status: MasteryStatus
    if not ordered:
        status = "exposed" if exps or prior.mastery > 0 else "unknown"
    elif gated:
        successes = [i for i, e in enumerate(gated) if e.score >= threshold]
        failures = [i for i, e in enumerate(gated) if e.score < threshold]
        last_success = successes[-1] if successes else -1
        last_failure = failures[-1] if failures else -1
        if (
            p_mastery >= params.mastered_min_p_mastery
            and len(families) >= params.min_distinct_families
            and s_sum >= params.proof_min_weight
            and s_sum >= 2 * f_sum
            and last_success > last_failure
        ):
            status = "mastered"
        elif f_sum >= params.fail_min_weight and f_sum >= 2 * s_sum and last_failure > last_success:
            status = "struggling"
        else:
            status = "learning"
    else:
        status = "learning"

    # Retention (Т-§8.3): stability is set by the first gated success and changes only on
    # delayed gated attempts (previous practice or exposure older than delay_threshold).
    delay_ms = params.delay_threshold_hours * MS_PER_HOUR
    stability = 0.0
    last_reviewed = 0
    previous_at: int | None = None
    timeline: list[tuple[tuple[int, int, str], EvidenceTrace | None]] = [
        (_order(e), e) for e in ordered
    ]
    timeline += [(_order(x), None) for x in exps]
    timeline.sort(key=lambda item: item[0])
    for (at, _, _), e in timeline:
        if e is not None and _is_gated(e):
            delayed = previous_at is not None and at - previous_at > delay_ms
            if e.score >= threshold:
                if stability == 0.0:
                    stability = params.initial_stability_days
                elif delayed:
                    quality = e.score * e.independence
                    stability *= 1.0 + (params.stability_success_multiplier - 1.0) * quality
                last_reviewed = at
            elif stability > 0.0 and delayed:
                stability = max(
                    params.min_stability_days, stability * params.stability_lapse_factor
                )
        previous_at = at

    next_review = 0
    decay_rate = 0.0
    if stability > 0:
        next_review = last_reviewed + review_interval_ms(stability, params.target_retention)
        decay_rate = 1.0 - retrievability(stability, MS_PER_DAY)
    return SkillFoldResult(
        p_mastery=p_mastery,
        confidence=confidence,
        evidence_count=len(ordered),
        distinct_families=len(families),
        decay_rate=decay_rate,
        stability=stability,
        last_practiced_at=ordered[-1].at_ms if ordered else 0,
        last_reviewed_at=last_reviewed,
        next_review_at=next_review,
        mastery_status=status,
        exposure_count=len(exps),
        last_exposed_at=exps[-1].at_ms if exps else 0,
    )


def _source_key(e: EvidenceTrace) -> str:
    return e.family_id if e.family_id != "unknown" else f"seq:{e.seq}:{e.id}"


def _confirmable(manifestations: Sequence[EvidenceTrace]) -> bool:
    """Two manifestations from different sources, at least one of them T1/T2 (Т-§9.2)."""
    return len({_source_key(e) for e in manifestations}) >= 2 and any(
        _is_gated(e) for e in manifestations
    )


def recompute_misconception_fold(
    manifestations: Sequence[EvidenceTrace],
    skill_evidence: Sequence[EvidenceTrace],
    resolved_at_ms: int,
    resolved_seq: int,
    params: FoldParams,
) -> MisconceptionFoldResult:
    mans = sorted((e for e in manifestations if not e.superseded), key=_order)
    # Resistances: clean gated successes on the misconception's skill.
    resists = sorted(
        (
            e
            for e in skill_evidence
            if not e.superseded
            and e.kind == "skill"
            and _is_gated(e)
            and e.score >= params.resist_min_score
            and e.independence >= params.resist_min_independence
        ),
        key=_order,
    )
    # Timeline kinds: 0 = manifestation, 1 = resistance, 2 = explicit resolution.
    timeline: list[tuple[tuple[int, int, str], int, EvidenceTrace | None]] = [
        (_order(e), 0, e) for e in mans
    ]
    timeline += [(_order(e), 1, e) for e in resists]
    if resolved_at_ms > 0:
        timeline.append(((resolved_at_ms, resolved_seq, ""), 2, None))
    timeline.sort(key=lambda item: (item[0], item[1]))

    watch_ms = params.watch_days * MS_PER_DAY
    state: MisconceptionStatus = "latent"
    seen: list[EvidenceTrace] = []
    resist_families: set[str] = set()
    watch_since = fixed_at = resist_count = 0
    for (at, _, _), kind, e in timeline:
        if kind == 0 and e is not None:
            seen.append(e)
            resist_families.clear()
            if state in ("watching", "fixed"):
                state = "confirmed"
            elif state in ("dismissed", "resolved", "latent"):
                state = "suspected"
            if state == "suspected" and _confirmable(seen):
                state = "confirmed"
        elif kind == 1 and e is not None:
            if state in ("latent", "resolved", "dismissed", "fixed"):
                continue
            resist_count += 1
            if e.family_id != "unknown":
                resist_families.add(e.family_id)
            if state == "confirmed" and len(resist_families) >= params.fix_min_resists:
                state = "watching"
                watch_since = at
            elif state == "suspected" and len(resist_families) >= params.fix_min_resists:
                state = "dismissed"
            elif state == "watching" and at - watch_since >= watch_ms:
                state = "fixed"
                fixed_at = at
        elif kind == 2:
            state = "resolved"
    return MisconceptionFoldResult(
        status=state,
        occurrences=len(mans),
        first_seen_at=mans[0].at_ms if mans else 0,
        last_seen_at=mans[-1].at_ms if mans else 0,
        watch_since_at=watch_since,
        fixed_at=fixed_at,
        resist_count=resist_count,
    )
