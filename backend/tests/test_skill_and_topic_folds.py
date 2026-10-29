"""Pure folds of Phase B and C (Т-§7–9, spec §4.3, Т-§22.2)."""

from __future__ import annotations

import itertools
from dataclasses import replace

import pytest

from quack.config import FoldParams
from quack.graph.folds import (
    MS_PER_DAY,
    MS_PER_HOUR,
    EvidenceTrace,
    ExposureTrace,
    SkillPrior,
    effective_mastery,
    recompute_misconception_fold,
    recompute_skill_fold,
    retrievability,
    review_interval_ms,
    skill_is_rusty,
)
from quack.graph.topic_display import PrereqSnapshot, SkillSnapshot, recompute_topic_display

P = FoldParams()
T0 = 1_790_000_000_000
_ids = itertools.count(1)


def task_ev(
    at: int,
    score: float = 1.0,
    *,
    family: str = "f1",
    tier: int = 1,
    independence: float = 1.0,
    depth: float = 0.5,
    superseded: bool = False,
    kind: str = "skill",
) -> EvidenceTrace:
    n = next(_ids)
    return EvidenceTrace(
        id=f"e{n}",
        seq=n,
        at_ms=at,
        kind=kind,  # type: ignore[arg-type]
        tier=tier,
        source_type="task",
        score=score,
        independence=independence,
        tested_depth=depth,
        family_id=family,
        superseded=superseded,
    )


def extractor_ev(at: int, direction: str = "positive", strength: float = 1.0) -> EvidenceTrace:
    n = next(_ids)
    score = {"positive": 1.0, "negative": 0.0, "neutral": 0.5}[direction]
    return EvidenceTrace(
        id=f"x{n}",
        seq=n,
        at_ms=at,
        kind="skill",
        tier=3,
        source_type="extractor",
        score=score,
        strength=strength,
        direction=direction,
    )


def exposure(at: int, depth: float = 1.0) -> ExposureTrace:
    n = next(_ids)
    return ExposureTrace(id=f"x{n}", seq=n, at_ms=at, depth=depth)


NO_PRIOR = SkillPrior()


def fold(evidence=(), exposures=(), prior=NO_PRIOR):
    return recompute_skill_fold(list(evidence), list(exposures), prior, P)


def mastered_pair(at: int = T0, **kw):
    return [task_ev(at, family="f1", **kw), task_ev(at + 60_000, family="f2", **kw)]


# --- Exposure-only ceiling -------------------------------------------------------------


def test_nothing_is_unknown() -> None:
    result = fold()
    assert result.mastery_status == "unknown"
    assert result.p_mastery == 0.0 and result.confidence == 0.0 and result.evidence_count == 0


@pytest.mark.parametrize("n", [1, 5, 50, 500])
def test_explanations_alone_never_exceed_exposure_ceiling(n: int) -> None:
    result = fold(exposures=[exposure(T0 + i * 1000, depth=1.0) for i in range(n)])
    assert result.p_mastery <= P.exposure_mastery_ceiling
    assert result.mastery_status == "exposed"
    assert result.exposure_count == n
    assert result.evidence_count == 0
    assert result.last_exposed_at == T0 + (n - 1) * 1000


def test_exposure_gain_grows_until_ceiling() -> None:
    one = fold(exposures=[exposure(T0, depth=1.0)])
    two = fold(exposures=[exposure(T0, depth=1.0), exposure(T0 + 1, depth=1.0)])
    assert 0.0 < one.p_mastery < two.p_mastery <= P.exposure_mastery_ceiling


def test_ungated_observations_never_open_mastery() -> None:
    evidence = [extractor_ev(T0 + i * 1000) for i in range(100)]
    result = fold(evidence, exposures=[exposure(T0 + i) for i in range(100)])
    assert result.p_mastery <= P.ungated_mastery_ceiling
    assert result.mastery_status == "learning"
    assert result.evidence_count == 100


def test_negative_model_observations_do_not_worsen_state() -> None:
    base = mastered_pair()
    noisy = base + [extractor_ev(T0 + 3_600_000 + i, "negative") for i in range(5)]
    a, b = fold(base), fold(noisy)
    assert b.p_mastery == a.p_mastery
    assert b.mastery_status == a.mastery_status == "mastered"
    assert b.evidence_count == a.evidence_count + 5


def test_prior_is_capped() -> None:
    result = fold(prior=SkillPrior(mastery=1.0, confidence=1.0))
    assert 0.0 < result.p_mastery <= P.prior_mastery_ceiling
    assert result.mastery_status == "exposed"


# --- Family diversity ------------------------------------------------------------------


@pytest.mark.parametrize("n", [1, 3, 10, 40])
def test_single_family_never_mastered(n: int) -> None:
    result = fold([task_ev(T0 + i * 60_000, family="only") for i in range(n)])
    assert result.distinct_families == 1
    assert result.p_mastery <= P.single_family_mastery_ceiling
    assert result.mastery_status != "mastered"


def test_second_family_unlocks_mastered() -> None:
    same = [task_ev(T0 + i * 60_000, family="f1") for i in range(5)]
    result = fold([*same, task_ev(T0 + 10 * 60_000, family="f2")])
    assert result.distinct_families == 2
    assert result.p_mastery >= P.mastered_min_p_mastery
    assert result.mastery_status == "mastered"


def test_unknown_family_does_not_count() -> None:
    result = fold([task_ev(T0, family="f1"), task_ev(T0 + 1, family="unknown")])
    assert result.distinct_families == 1
    assert result.mastery_status != "mastered"


def test_one_independent_task_starting_values() -> None:
    # w = 1.0 * 1.0 * (0.6 + 0.4 * 0.5) = 0.8; confidence = 0.8 / 1.3; capped to 0.6.
    result = fold([task_ev(T0)])
    assert result.confidence == pytest.approx(0.8 / 1.3)
    assert result.p_mastery == pytest.approx(P.single_family_mastery_ceiling)
    assert result.mastery_status == "learning"


def test_two_family_pair_starting_values() -> None:
    result = fold(mastered_pair())
    # W = 1.6, confidence = 1.6 / 2.1, ratio = 1.
    assert result.confidence == pytest.approx(1.6 / 2.1)
    assert result.p_mastery == pytest.approx(1.6 / 2.1)
    assert result.mastery_status == "mastered"


# --- Independence and depth ------------------------------------------------------------


def test_hints_give_smaller_gain_than_independent_solving() -> None:
    independent = fold(mastered_pair(independence=1.0))
    hinted = fold(mastered_pair(independence=0.5))
    assert hinted.p_mastery < independent.p_mastery
    assert hinted.confidence < independent.confidence
    assert hinted.mastery_status != "mastered"


def test_deeper_testing_gives_larger_gain() -> None:
    deep = fold(mastered_pair(depth=1.0))
    shallow = fold(mastered_pair(depth=0.0))
    assert deep.p_mastery > shallow.p_mastery


def test_tier2_counts_less_than_tier1() -> None:
    t1 = fold(mastered_pair(tier=1))
    t2 = fold(mastered_pair(tier=2))
    assert t2.p_mastery < t1.p_mastery


def test_failures_after_success_lower_mastery_and_recency_matters() -> None:
    ok = mastered_pair()
    failed_last = fold([*ok, task_ev(T0 + 120_000, 0.0, family="f3")])
    failed_first = fold([task_ev(T0 - 120_000, 0.0, family="f3"), *ok])
    assert failed_last.p_mastery < fold(ok).p_mastery
    # λ-decay: the latest evidence weighs most.
    assert failed_first.p_mastery > failed_last.p_mastery
    assert failed_last.mastery_status != "mastered"


def test_consistent_failures_are_struggling() -> None:
    result = fold([task_ev(T0 + i * 60_000, 0.0, family=f"f{i}") for i in range(3)])
    assert result.mastery_status == "struggling"
    assert result.p_mastery == 0.0


# --- Decay and stability ---------------------------------------------------------------


def test_retrievability_curve() -> None:
    assert retrievability(5.0, 0) == pytest.approx(1.0)
    assert retrievability(5.0, 5 * MS_PER_DAY) == pytest.approx(0.9)
    assert retrievability(5.0, 10 * MS_PER_DAY) < retrievability(5.0, 5 * MS_PER_DAY)
    assert retrievability(10.0, 7 * MS_PER_DAY) > retrievability(2.0, 7 * MS_PER_DAY)
    assert review_interval_ms(3.0, 0.9) == 3 * MS_PER_DAY


def test_first_success_sets_stability_and_review_date() -> None:
    result = fold(mastered_pair())
    assert result.stability == P.initial_stability_days
    assert result.last_reviewed_at == T0 + 60_000
    assert result.next_review_at == T0 + 60_000 + review_interval_ms(
        P.initial_stability_days, P.target_retention
    )
    assert result.last_practiced_at == T0 + 60_000
    assert 0.0 < result.decay_rate < 1.0


def test_effective_mastery_decays_after_review_date_and_skill_becomes_rusty() -> None:
    result = fold(mastered_pair())
    now = result.last_reviewed_at
    assert effective_mastery(result.p_mastery, result.stability, result.last_reviewed_at, now) == (
        pytest.approx(result.p_mastery)
    )
    later = result.next_review_at + 5 * MS_PER_DAY
    decayed = effective_mastery(result.p_mastery, result.stability, result.last_reviewed_at, later)
    assert decayed < result.p_mastery
    assert not skill_is_rusty(result, result.next_review_at - 1)
    assert skill_is_rusty(result, result.next_review_at + 1)


def test_delayed_success_grows_stability_and_restores() -> None:
    first = fold(mastered_pair())
    gap = first.next_review_at + 3 * MS_PER_DAY
    second = fold([*mastered_pair(), task_ev(gap, family="f3")])
    assert second.stability > first.stability
    assert second.last_reviewed_at == gap
    assert (second.next_review_at - second.last_reviewed_at) > (
        first.next_review_at - first.last_reviewed_at
    )
    assert not skill_is_rusty(second, gap)
    assert second.mastery_status == "mastered"


def test_accumulated_stability_recovers_faster() -> None:
    # A: spaced reviews accumulate stability; B: crammed in one day.
    spaced = [
        task_ev(T0, family="f1"),
        task_ev(T0 + 60_000, family="f2"),
        task_ev(T0 + 2 * MS_PER_DAY, family="f3"),
        task_ev(T0 + 6 * MS_PER_DAY, family="f4"),
    ]
    crammed = [task_ev(T0 + 6 * MS_PER_DAY + i * 60_000, family=f"f{i}") for i in range(4)]
    a, b = fold(spaced), fold(crammed)
    assert a.stability > b.stability
    check_at = T0 + 30 * MS_PER_DAY
    eff_a = effective_mastery(a.p_mastery, a.stability, a.last_reviewed_at, check_at)
    eff_b = effective_mastery(b.p_mastery, b.stability, b.last_reviewed_at, check_at)
    assert eff_a > eff_b


def test_failure_after_delay_shrinks_stability() -> None:
    base = mastered_pair()
    later = T0 + 10 * MS_PER_DAY
    result = fold([*base, task_ev(later, 0.0, family="f3")])
    assert result.stability == pytest.approx(
        max(P.min_stability_days, P.initial_stability_days * P.stability_lapse_factor)
    )


def test_same_day_repetition_does_not_grow_stability() -> None:
    result = fold([*mastered_pair(), task_ev(T0 + 2 * MS_PER_HOUR, family="f3")])
    assert result.stability == P.initial_stability_days


# --- Superseded evidence ---------------------------------------------------------------


@pytest.mark.parametrize("position", [0, 1, 2, 3])
def test_superseded_evidence_is_as_if_never_existed(position: int) -> None:
    history = [
        task_ev(T0, 1.0, family="f1"),
        task_ev(T0 + MS_PER_DAY, 0.0, family="f2", independence=0.5),
        task_ev(T0 + 2 * MS_PER_DAY, 1.0, family="f3"),
        task_ev(T0 + 5 * MS_PER_DAY, 1.0, family="f2"),
    ]
    marked = list(history)
    marked[position] = replace(history[position], superseded=True)
    without = history[:position] + history[position + 1 :]
    exposures = [exposure(T0 - 1000)]
    assert fold(marked, exposures) == fold(without, exposures)


# --- Misconceptions --------------------------------------------------------------------


def manifestation(at: int, *, tier: int = 3, family: str = "unknown") -> EvidenceTrace:
    return task_ev(at, 0.0, family=family, tier=tier, kind="manifestation")


def mfold(manifestations, skill_evidence=(), resolved_at=0, resolved_seq=0):
    return recompute_misconception_fold(
        list(manifestations), list(skill_evidence), resolved_at, resolved_seq, P
    )


def test_no_manifestation_is_latent() -> None:
    assert mfold([]).status == "latent"


def test_repeated_observations_activate_misconception() -> None:
    one = mfold([manifestation(T0)])
    assert one.status == "suspected" and one.occurrences == 1
    two = mfold([manifestation(T0), manifestation(T0 + 1000)])
    assert two.status == "suspected"  # model-only observations cannot confirm (law 4)
    confirmed = mfold(
        [manifestation(T0), manifestation(T0 + 1000), manifestation(T0 + 2000, tier=1, family="a")]
    )
    assert confirmed.status == "confirmed"
    assert confirmed.occurrences == 3
    assert confirmed.first_seen_at == T0 and confirmed.last_seen_at == T0 + 2000


def _confirmed(at: int = T0):
    return [manifestation(at, tier=1, family="a"), manifestation(at + 1000, tier=1, family="b")]


def test_clean_practice_moves_to_watching_then_fixed() -> None:
    mans = _confirmed()
    r1 = task_ev(T0 + MS_PER_DAY, family="c")
    r2 = task_ev(T0 + MS_PER_DAY + 1000, family="d")
    watching = mfold(mans, [r1, r2])
    assert watching.status == "watching"
    assert watching.watch_since_at == r2.at_ms
    too_early = task_ev(r2.at_ms + MS_PER_DAY, family="e")
    assert mfold(mans, [r1, r2, too_early]).status == "watching"
    late = task_ev(r2.at_ms + int(P.watch_days * MS_PER_DAY) + 1, family="e")
    fixed = mfold(mans, [r1, r2, late])
    assert fixed.status == "fixed"
    assert fixed.fixed_at == late.at_ms


def test_resistances_need_distinct_families_and_clean_practice() -> None:
    mans = _confirmed()
    same_family = [task_ev(T0 + MS_PER_DAY + i, family="c") for i in range(5)]
    assert mfold(mans, same_family).status == "confirmed"
    hinted = [
        task_ev(T0 + MS_PER_DAY, family="c", independence=0.5),
        task_ev(T0 + MS_PER_DAY + 1, family="d", independence=0.5),
    ]
    assert mfold(mans, hinted).status == "confirmed"
    before = [task_ev(T0 - 2000, family="c"), task_ev(T0 - 1000, family="d")]
    assert mfold(mans, before).status == "confirmed"


def test_new_manifestation_returns_watching_to_confirmed() -> None:
    mans = _confirmed()
    resists = [task_ev(T0 + MS_PER_DAY, family="c"), task_ev(T0 + MS_PER_DAY + 1, family="d")]
    again = manifestation(T0 + 2 * MS_PER_DAY)
    assert mfold([*mans, again], resists).status == "confirmed"


def test_suspected_dismissed_by_clean_practice() -> None:
    resists = [task_ev(T0 + MS_PER_DAY, family="c"), task_ev(T0 + MS_PER_DAY + 1, family="d")]
    dismissed = mfold([manifestation(T0)], resists)
    assert dismissed.status == "dismissed"
    again = mfold([manifestation(T0), manifestation(T0 + 2 * MS_PER_DAY)], resists)
    assert again.status == "suspected"


def test_explicit_resolution_closes_and_new_manifestation_reopens() -> None:
    mans = _confirmed()
    resolved = mfold(mans, [], resolved_at=T0 + MS_PER_DAY, resolved_seq=100)
    assert resolved.status == "resolved"
    reopened = mfold(
        [*mans, manifestation(T0 + 2 * MS_PER_DAY)],
        [],
        resolved_at=T0 + MS_PER_DAY,
        resolved_seq=100,
    )
    assert reopened.status == "confirmed"


def test_superseded_manifestation_ignored() -> None:
    mans = [*_confirmed(), replace(manifestation(T0 + 5000, tier=1, family="z"), superseded=True)]
    assert mfold(mans) == mfold(_confirmed())


# --- Topic display ---------------------------------------------------------------------


def snap(
    sid: str,
    p: float = 0.0,
    *,
    conf: float = 0.0,
    evidence: int = 0,
    exposures: int = 0,
    status: str = "active",
    mastery_status: str = "unknown",
    next_review: int = 0,
    last_practiced: int = 0,
) -> SkillSnapshot:
    return SkillSnapshot(
        skill_id=sid,
        status=status,
        p_mastery=p,
        confidence=conf,
        evidence_count=evidence,
        exposure_count=exposures,
        mastery_status=mastery_status,
        next_review_at=next_review,
        last_practiced_at=last_practiced,
    )


def topic(skills, deps=None, prereqs=(), as_of=T0):
    return recompute_topic_display(list(skills), deps or {}, list(prereqs), as_of, P)


def test_hub_score_weights_mastery() -> None:
    skills = [snap("a", 0.9, evidence=2), snap("b", 0.3, evidence=1), snap("c", 0.0)]
    result = topic(skills, deps={"a": 2})
    w_a = 1 + P.hub_weight_factor * 2
    assert result.hub_score == 2
    assert result.mastery == pytest.approx((w_a * 0.9 + 0.3 + 0.0) / (w_a + 2))
    assert result.coverage == pytest.approx(2 / 3)
    assert result.skill_count == 3
    assert result.mastery > topic(skills).mastery


def test_archived_and_merged_skills_are_excluded() -> None:
    skills = [
        snap("a", 0.8, evidence=1),
        snap("b", 0.0, status="archived"),
        snap("c", status="merged"),
    ]
    result = topic(skills)
    assert result.skill_count == 1
    assert result.mastery == pytest.approx(0.8)
    assert result.coverage == 1.0


def test_locked_when_prerequisite_not_mastered() -> None:
    result = topic([snap("a")], prereqs=[PrereqSnapshot("t0", "active", 0.2)])
    assert result.display_state == "locked"


def test_available_when_prerequisites_ready_or_inactive() -> None:
    assert topic([snap("a")]).display_state == "available"
    ready = topic([snap("a")], prereqs=[PrereqSnapshot("t0", "active", 0.9)])
    assert ready.display_state == "available"
    archived = topic([snap("a")], prereqs=[PrereqSnapshot("t0", "archived", 0.0)])
    assert archived.display_state == "available"


def test_in_progress_with_any_activity() -> None:
    exposed = topic([snap("a", 0.1, exposures=1)], prereqs=[PrereqSnapshot("t0", "active", 0.1)])
    assert exposed.display_state == "in_progress"
    practiced = topic([snap("a", 0.5, evidence=1), snap("b")])
    assert practiced.display_state == "in_progress"


def test_solid_and_rusty() -> None:
    due = T0 + 3 * MS_PER_DAY
    skills = [
        snap("a", 0.8, evidence=2, mastery_status="mastered", next_review=due),
        snap("b", 0.75, evidence=2, mastery_status="mastered", next_review=due + MS_PER_DAY),
    ]
    solid = topic(skills, as_of=T0)
    assert solid.display_state == "solid"
    assert solid.nearest_review_at == due
    assert topic(skills, as_of=due + 1).display_state == "rusty"


def test_struggling_skill_blocks_solid() -> None:
    skills = [
        snap("a", 0.95, evidence=3, mastery_status="mastered"),
        snap("b", 0.95, evidence=3, mastery_status="mastered"),
        snap("c", 0.95, evidence=3, mastery_status="mastered"),
        snap("d", 0.95, evidence=3, mastery_status="mastered"),
        snap("e", 0.0, evidence=3, mastery_status="struggling"),
    ]
    assert topic(skills).display_state == "in_progress"


def test_low_coverage_blocks_solid() -> None:
    skills = [snap("a", 1.0, evidence=2, mastery_status="mastered")] + [
        snap(f"s{i}", 0.9) for i in range(3)
    ]
    assert topic(skills).display_state == "in_progress"


def test_empty_topic_is_available_with_zero_aggregates() -> None:
    result = topic([])
    assert result.display_state == "available"
    assert result.mastery == 0.0 and result.coverage == 0.0 and result.skill_count == 0
