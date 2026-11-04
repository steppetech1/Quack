"""`calibration.weekly` (ТЗ Ф4 WP7.5; Т-§18.2, Т-§18.5; D-A-219, D-A-220, D-A-245): the Brier
score, the curve, the proposed `p_*` and the report of the tier weights of the week before the
period equal the hand count; nothing is written. The rule of the weights is tested on sums."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.config import Settings
from quack.db import Db
from quack.events.event_types import AnswerSubmitted, TaskCreated
from quack.events.log import NewEvent, append_events
from quack.events.records import TaskMarkupRecord, TestedLevelRecord
from quack.events.values import AnswerSpec, LearnerAnswer, MathValue, Verdict
from quack.graph.calibration_read import TierEvidence
from quack.graph.read import GraphReader, StateAt
from quack.jobs.calibration_p4 import (
    TierSums,
    WeightRule,
    calibrate_weights,
    calibration_proposal,
    week_before,
)
from tests.db.fixtures import create_learner
from tests.support import graph_read as fx

SETTINGS = Settings(calibration_min_reports=2)
CURRENT = (1.0, 0.8, 0.4, 0.15)
RULE = WeightRule()


class _RecallReader(fx.FixedGraphReader):
    """The fixed memory where every skill is recalled with the probability 0.9."""

    async def state_at(self, learner_id: UUID, seq_n: int, skill_ids: Any) -> StateAt:
        state = await super().state_at(learner_id, seq_n, skill_ids)
        skills = [s.model_copy(update={"recall_probability": 0.9}) for s in state.skills]
        return state.model_copy(update={"skills": skills})


# (tier, outcome, assistance, level, sourceSeq) of the evidence of each skill (level 3 counts).
_NOISE = [
    (1, "incorrect", "none", 2, 0),  # another level
    (1, "incorrect", "none", 3, 10**9),  # after the answer
    (1, "partial", "none", 3, 0),  # not decided
    (1, "incorrect", "shown_solution", 3, 0),  # the solution was shown
]
EVIDENCE: dict[UUID, list[tuple[int, str, str, int, int]]] = {
    fx.SKILL_PROVEN: [
        (1, "correct", "none", 3, 0),
        (3, "correct", "none", 3, 0),
        (3, "incorrect", "none", 3, 0),
        *_NOISE,
    ],
    fx.SKILL_FAILED: [(1, "incorrect", "none", 3, 0), (3, "correct", "none", 3, 0), *_NOISE[:2]],
    fx.SKILL_QUIET: [(3, "incorrect", "none", 3, 0), *_NOISE],
}


async def _attempt(
    db: Db, learner_id: UUID, skill_id: UUID, outcome: str, at: datetime, key: str
) -> None:
    session_id, task_id = uuid4(), uuid4()
    markup = TaskMarkupRecord(
        tests=[TestedLevelRecord(skill_id=skill_id, level=3)],
        method_named=False,
        answer=AnswerSpec(type="number", value=MathValue(latex="1", sympy="1")),
    )

    def event(type_: str, payload: Any, tail: str) -> NewEvent:
        return NewEvent(
            type=type_,
            payload=payload,
            actor="learner" if type_ == "answer.submitted" else "system",
            idempotency_key=f"{key}-{tail}",
            session_id=session_id,
            domain_id=fx.DOMAIN,
            occurred_at=at,
        )

    stored = await append_events(
        db,
        learner_id,
        [
            event(
                "task.created",
                TaskCreated(
                    task_id=task_id,
                    family_id=uuid4(),
                    source="teacher",
                    markup=markup,
                    verification="checked",
                ),
                "t",
            ),
            event(
                "answer.submitted",
                AnswerSubmitted(
                    task_id=task_id,
                    answer=LearnerAnswer(kind="number", value="1"),
                    source="chat",
                ),
                "a",
            ),
        ],
    )
    verdict = Verdict(
        answer_seq=stored[-1].seq,
        part="main",
        outcome=outcome,  # type: ignore[arg-type]  # a literal of the model
        method="exact",
        verification="code",
    )
    await append_events(db, learner_id, [event("verdict.recorded", verdict, "v")])


async def test_calibration_proposal_matches_the_hand_count(pg_db: Db) -> None:
    since, until = week_before("2026-46")
    assert (since, until) == (
        datetime(2026, 11, 2, tzinfo=UTC),
        datetime(2026, 11, 9, tzinfo=UTC),
    )
    inside = since + timedelta(days=2)
    first, second = await create_learner(pg_db), await create_learner(pg_db)
    proven, failed, quiet = fx.SKILL_PROVEN, fx.SKILL_FAILED, fx.SKILL_QUIET
    attempts = [
        (first, proven, "correct"),
        (first, proven, "incorrect"),
        (first, failed, "incorrect"),
        (second, proven, "correct"),
        (second, failed, "correct"),
        (second, quiet, "incorrect"),
    ]
    for i, (learner_id, skill_id, outcome) in enumerate(attempts):
        await _attempt(pg_db, learner_id, skill_id, outcome, inside, f"in{i}")
    # A week earlier: not in the window.
    await _attempt(pg_db, first, proven, "incorrect", since - timedelta(days=1), "old")
    events_before = (await pg_db.fetch_one("SELECT count(*) AS n FROM core.events"))["n"]  # type: ignore[index]

    def reader(_learner: UUID) -> GraphReader:
        return _RecallReader()

    async def evidence(_learner: UUID, skills: Sequence[UUID]) -> list[TierEvidence]:
        found: list[TierEvidence] = []
        for skill in skills:
            for tier, outcome, assistance, level, seq in EVIDENCE[skill]:
                found.append(TierEvidence(skill, level, tier, outcome, assistance, seq))
        return found

    # min_units 2: the six units of the hand count are enough for the rule.
    result = await calibration_proposal(
        pg_db, SETTINGS, reader, evidence, since, until, WeightRule(min_units=2)
    )

    # p_proven × R = 0.9 × 0.9 = 0.81 on outcomes 1, 0, 1; p_failed 0.2 on 0, 1; p_unknown 0.4 on 0.
    assert result["attempts"] == 6 and result["observations"] == 6
    assert result["brier"] == pytest.approx((2 * 0.19**2 + 0.81**2 + 0.2**2 + 0.8**2 + 0.4**2) / 6)
    rules = result["rules"]
    assert rules["p_proven"]["brier"] == pytest.approx((2 * 0.19**2 + 0.81**2) / 3)
    assert rules["p_proven"]["calibration"] == [{"predicted": 0.8, "observed": 2 / 3, "of": 3}]
    assert rules["p_proven"]["proposed"] == pytest.approx(2 / (3 * 0.9))
    assert rules["p_failed"]["observed_rate"] == pytest.approx(0.5)
    assert rules["p_failed"]["proposed"] == pytest.approx(0.5)
    # One observation of `p_unknown` is under `calibration_min_reports`: no proposal.
    assert rules["p_unknown"]["observations"] == 1 and rules["p_unknown"]["proposed"] is None
    assert rules["p_derived"]["observations"] == 0 and rules["p_derived"]["brier"] is None
    # Tier weights, units (skill, level 3): outcome y; T1 evidence: proven correct (x = +0.5),
    # failed incorrect (x = -0.5), quiet none; T3: proven one correct and one incorrect (tie),
    # failed correct (+0.5), quiet incorrect (-0.5). The noise (level 2, seq 10**9, partial,
    # shown solution) is not counted.
    # T1: proven y 1, 0, 1 -> xy' +0.25, -0.25, +0.25; failed y 0, 1 -> +0.25, -0.25. n = 5,
    #   Σx² = 1.25, Σxy' = 0.25, w* = 0.2, Σy'² = 1.25, RSS = 1.25 - 0.25²/1.25 = 1.2,
    #   se = sqrt(1.2 / 4 / 1.25) = sqrt(0.24).
    # T3: proven 3 ties; failed (+0.5) y 0, 1 -> -0.25, +0.25; quiet (-0.5) y 0 -> +0.25. n = 3,
    #   Σx² = 0.75, Σxy' = 0.25, w* = 1/3, RSS = 0.75 - 0.25²/0.75 = 2/3, se = sqrt(2/3 / 2 / 0.75).
    weights = result["evidence_weights"]
    assert weights["current"] == {"T1": 1.0, "T2": 0.8, "T3": 0.4, "T4": 0.15}
    assert weights["reference_strength"] == pytest.approx(0.2)
    tiers = weights["tiers"]
    assert (tiers["T1"]["units"], tiers["T1"]["ties"]) == (5, 0)
    assert tiers["T1"]["strength"] == pytest.approx(0.2)
    assert tiers["T1"]["se"] == pytest.approx(0.24**0.5)
    assert (tiers["T3"]["units"], tiers["T3"]["ties"]) == (3, 3)
    assert tiers["T3"]["strength"] == pytest.approx(1 / 3)
    assert tiers["T3"]["se"] == pytest.approx((4 / 9) ** 0.5)
    assert tiers["T3"]["relative"] == pytest.approx(5 / 3)
    for name in ("T2", "T4"):
        assert tiers[name]["units"] == 0 and tiers[name]["strength"] is None
    # 0.2 - 1.645 · sqrt(0.24) < 0: the scale is not positive with confidence, no proposal.
    assert result["evidence_weights_reason"] is not None
    assert all(t["proposed"] is None and t["direction"] != "below_current" for t in tiers.values())
    # Reading only: no event, no new value of the settings.
    events_after = (await pg_db.fetch_one("SELECT count(*) AS n FROM core.events"))["n"]  # type: ignore[index]
    assert events_after == events_before
    assert SETTINGS.p_proven == 0.9 and SETTINGS.p_failed == 0.2
    assert SETTINGS.fold.tier_base_weights == CURRENT


def _sums(units: int, ties: int, sxx: float, sxy: float) -> TierSums:
    """Sums of `units` units; `Σy'² = 0.25 · units` because `y' = ±0.5`."""
    return TierSums(units=units, ties=ties, sxx=sxx, sxy=sxy, syy=0.25 * units)


# 40 units with x = ±0.5 (Σx² = 10); `sxy = 0.25 · (agree − disagree)`:
# T1 all 40 agree: Σxy' = 10, w* = 1, RSS = 10 - 100/10 = 0, se = 0 (the scale 1.0).
# T2 35 agree, 5 not: Σxy' = 7.5, w* = 0.75, RSS = 10 - 56.25/10 = 4.375,
#   se = sqrt(4.375 / 39 / 10) = 0.10592; upper ρ = 0.75 + 1.645 · 0.10592 = 0.924 >= 0.8: keep.
# T3 20 agree, 20 not: Σxy' = 0, w* = 0, RSS = 10, se = sqrt(10 / 39 / 10) = 0.16013;
#   upper ρ = 1.645 · 0.16013 = 0.2634 < 0.4: lowered to max(ρ = 0, 0.4 · 0.5, 0.05) = 0.2.
# T4 10 units: under min_units 30, insufficient.
def test_the_weak_tier_is_lowered_by_one_step_and_the_strong_one_kept() -> None:
    sums = {
        1: _sums(40, 0, 10, 10),
        2: _sums(40, 0, 10, 7.5),
        3: _sums(40, 2, 10, 0),
        4: _sums(10, 0, 2.5, 0),
    }
    weights, reason = calibrate_weights(sums, CURRENT, RULE)
    tiers = weights["tiers"]
    assert reason is None and weights["reference_strength"] == pytest.approx(1.0)
    assert tiers["T1"]["direction"] == "reference" and tiers["T1"]["proposed"] is None
    assert tiers["T2"]["strength"] == pytest.approx(0.75)
    assert tiers["T2"]["se"] == pytest.approx((4.375 / 390) ** 0.5)
    assert tiers["T2"]["direction"] == "keep" and tiers["T2"]["proposed"] is None
    assert tiers["T3"]["strength"] == pytest.approx(0.0) and tiers["T3"]["ties"] == 2
    assert tiers["T3"]["se"] == pytest.approx((1 / 39) ** 0.5)
    assert tiers["T3"]["direction"] == "below_current"
    assert tiers["T3"]["proposed"] == pytest.approx(0.2) and not tiers["T3"]["capped_by_order"]
    assert tiers["T4"]["direction"] == "insufficient" and tiers["T4"]["proposed"] is None
    assert weights["current"] == {"T1": 1.0, "T2": 0.8, "T3": 0.4, "T4": 0.15}


def test_too_few_units_give_no_proposal() -> None:
    # T3 is the same weak tier as above, but 29 units: under min_units.
    sums = {1: _sums(40, 0, 10, 10), 2: _sums(29, 0, 7.25, 0), 3: _sums(29, 0, 7.25, 0)}
    sums[4] = TierSums()
    weights, reason = calibrate_weights(sums, CURRENT, RULE)
    assert reason is None
    for name in ("T2", "T3", "T4"):
        assert weights["tiers"][name]["direction"] == "insufficient"
        assert weights["tiers"][name]["proposed"] is None
    # The tier with no unit has no strength at all.
    assert weights["tiers"]["T4"]["strength"] is None and weights["tiers"]["T4"]["relative"] is None


def test_an_undefined_or_not_positive_scale_gives_no_proposal_and_a_reason() -> None:
    weak_t3 = _sums(40, 0, 10, 0)
    # T1 with a negative slope: w* = -1, the scale is not positive.
    negative = {1: _sums(40, 0, 10, -10), 2: weak_t3, 3: weak_t3, 4: weak_t3}
    weights, reason = calibrate_weights(negative, CURRENT, RULE)
    assert reason is not None and weights["reference_strength"] == pytest.approx(-1.0)
    # T1 without any unit: the strength is undefined.
    empty = {1: TierSums(), 2: weak_t3, 3: weak_t3, 4: weak_t3}
    weights_empty, reason_empty = calibrate_weights(empty, CURRENT, RULE)
    assert reason_empty is not None and weights_empty["reference_strength"] is None
    for report in (weights, weights_empty):
        assert all(report["tiers"][f"T{t}"]["proposed"] is None for t in (2, 3, 4))
        assert all(report["tiers"][f"T{t}"]["direction"] == "insufficient" for t in (2, 3, 4))


def test_a_stronger_tier_is_reported_but_never_raised() -> None:
    # T2 agrees on all 40 units: w* = 1, se = 0, the lower bound of ρ is 1.0 > 0.8.
    sums = {1: _sums(40, 0, 10, 10), 2: _sums(40, 0, 10, 10), 3: TierSums(), 4: TierSums()}
    weights, _ = calibrate_weights(sums, CURRENT, RULE)
    assert weights["tiers"]["T2"]["direction"] == "above_current"
    assert weights["tiers"]["T2"]["proposed"] is None


def test_the_order_of_tiers_caps_a_tier_that_weighs_more_than_the_one_above() -> None:
    # Current weights (1.0, 0.6, 0.5, 0.15). T2 has no link to the outcome: ρ = 0, lowered to
    # max(0, 0.6 · 0.5, 0.05) = 0.3. T3 has no data, but 0.5 > 0.3: capped to 0.3 by the order.
    sums = {1: _sums(40, 0, 10, 10), 2: _sums(40, 0, 10, 0), 3: TierSums(), 4: TierSums()}
    weights, _ = calibrate_weights(sums, (1.0, 0.6, 0.5, 0.15), RULE)
    tiers = weights["tiers"]
    assert tiers["T2"]["proposed"] == pytest.approx(0.3) and not tiers["T2"]["capped_by_order"]
    assert tiers["T3"]["direction"] == "insufficient"
    assert tiers["T3"]["proposed"] == pytest.approx(0.3) and tiers["T3"]["capped_by_order"]
    assert tiers["T4"]["proposed"] is None and not tiers["T4"]["capped_by_order"]
