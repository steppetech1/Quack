"""WP5.3: the fold of the learned part of the profile (Т-§9.5) — pure, deterministic."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from quack.events.log import StoredEvent
from quack.graph.profile_fold import (
    EvidenceSample,
    ProfileParams,
    collect_profile_inputs,
    fold_profile,
    pref_key,
    profile_node_props,
)

LEARNER = UUID("00000000-0000-0000-0000-0000000000a1")
SKILL = str(UUID("00000000-0000-0000-0000-0000000000b1"))
PARAMS = ProfileParams(profile_min_samples=10, calibration_window=200, calibration_min_reports=10)


def _event(seq: int, type_: str, payload: dict[str, Any]) -> StoredEvent:
    return StoredEvent(
        learner_id=LEARNER,
        seq=seq,
        id=seq,
        type=type_,
        schema_version=1,
        actor="test",
        session_id=None,
        domain_id=None,
        correlation_id=None,
        causation_seq=None,
        idempotency_key=f"k{seq}",
        payload_hash="h",
        payload=payload,
        occurred_at=datetime(2026, 11, 1, tzinfo=UTC),
        source_refs=[],
    )


def turn(
    seq: int, approach: str = "small_steps", branch: str | None = None, skills: tuple[str, ...] = ()
) -> StoredEvent:
    return _event(
        seq,
        "turn.committed",
        {
            "action_seq": seq - 1,
            "text": "t",
            "blocks": [{"block_id": "b1", "text": "t", "element_id": None}],
            "head": {
                "intent": "explain",
                "target_skill_ids": list(skills),
                "verdict_stance": None,
                "branch_type": branch,
                "approach": approach,
            },
            "markup": {
                "tasks": [],
                "elements": [],
                "exposures": [],
                "targets_misconception_id": None,
                "plan_update": {
                    "completed_steps": [],
                    "current_step": "s",
                    "next_steps": [],
                    "branches": [],
                    "cancel_request_ids": [],
                },
                "agent_requests": [],
                "corrections_acknowledged": [],
                "decision_reason": "r",
                "deadline": None,
            },
            "role": "teacher",
            "model": "m",
            "effort": "low",
            "degraded_mode": None,
            "guard_retries": 0,
            "manifest_id": str(uuid4()),
        },
    )


def answer(seq: int, text: str | None = None, confidence: int | None = None) -> StoredEvent:
    return _event(
        seq,
        "answer.submitted",
        {
            "task_id": str(uuid4()),
            "answer": {"kind": "number", "value": "1"},
            "text": text,
            "confidence": confidence,
            "source": "chat",
        },
    )


def evidence(
    source_seq: int,
    outcome: str = "correct",
    *,
    gate: bool = True,
    tier: int = 1,
    assist: str = "none",
) -> EvidenceSample:
    return EvidenceSample(
        source_seq=source_seq,
        skill_id=SKILL,
        tier=tier,
        outcome=outcome,  # type: ignore[arg-type]
        assistance=assist,
        passes_gate=gate,
    )


def learned(events: list[StoredEvent], ev: list[EvidenceSample], params: ProfileParams = PARAMS):
    return fold_profile(collect_profile_inputs(events, ev), params)


# --- calibration -----------------------------------------------------------------------------


def test_overconfident_learner_gets_brier_and_over() -> None:
    events = [answer(i, confidence=4) for i in range(1, 11)]
    fold = learned(events, [evidence(i, "incorrect") for i in range(1, 11)])
    assert fold.confidence_brier == 0.9025  # (0.95 - 0)^2
    assert fold.confidence_bias == "over"


def test_underconfident_learner_gets_under() -> None:
    events = [answer(i, confidence=1) for i in range(1, 11)]
    fold = learned(events, [evidence(i, "correct") for i in range(1, 11)])
    assert fold.confidence_brier == 0.5625  # (0.25 - 1)^2
    assert fold.confidence_bias == "under"


def test_perfect_mix_is_calibrated_zero_skew() -> None:
    # 0.5 against one success and one failure: skew 0, Brier 0.25.
    events = [answer(i, confidence=2) for i in range(1, 11)]
    ev = [evidence(i, "correct" if i % 2 else "incorrect") for i in range(1, 11)]
    fold = learned(events, ev)
    assert fold.confidence_brier == 0.25
    assert fold.confidence_bias == "calibrated"


def test_calibration_needs_min_reports_and_the_gate() -> None:
    events = [answer(i, confidence=3) for i in range(1, 10)]  # 9 reports
    assert learned(events, [evidence(i) for i in range(1, 10)]).confidence_brier is None
    # Ten reports, but one outcome does not pass the gate: nine pairs.
    events = [answer(i, confidence=3) for i in range(1, 11)]
    ev = [evidence(i) for i in range(1, 10)] + [evidence(10, gate=False)]
    fold = learned(events, ev)
    # Nor does `partial` count (it never opens the gate).
    partial = learned(events, [evidence(i) for i in range(1, 10)] + [evidence(10, "partial")])
    assert partial.confidence_brier is None
    assert fold.confidence_brier is None and fold.confidence_bias is None


def test_calibration_uses_the_last_window_only() -> None:
    params = ProfileParams(calibration_window=10, calibration_min_reports=10)
    # 10 old overconfident failures, then 10 well-calibrated-at-0.95 successes.
    events = [answer(i, confidence=4) for i in range(1, 21)]
    ev = [evidence(i, "incorrect") for i in range(1, 11)] + [evidence(i) for i in range(11, 21)]
    fold = learned(events, ev, params)
    assert fold.confidence_brier == 0.0025  # (0.95 - 1)^2
    assert fold.confidence_bias == "under"


def test_later_report_of_a_target_wins() -> None:
    events = [answer(i, confidence=4) for i in range(1, 11)]
    events += [
        _event(100 + i, "confidence.reported", {"target_seq": i, "value": 1}) for i in range(1, 11)
    ]
    fold = learned(events, [evidence(i, "incorrect") for i in range(1, 11)])
    assert fold.confidence_brier == 0.0625  # (0.25 - 0)^2


# --- the helpful approach --------------------------------------------------------------------


def _approach_journal(
    plan: dict[str, list[bool]],
) -> tuple[list[StoredEvent], list[EvidenceSample]]:
    """A turn with the approach, then one T1 attempt of the same skill, per planned result."""
    events: list[StoredEvent] = []
    ev: list[EvidenceSample] = []
    seq = 0
    for approach, results in plan.items():
        for ok in results:
            events.append(turn(seq + 1, approach, skills=(SKILL,)))
            ev.append(evidence(seq + 2, "correct" if ok else "incorrect"))
            seq += 3
    return events, ev


def test_helpful_approach_is_the_one_with_most_independent_successes() -> None:
    events, ev = _approach_journal(
        {"example_first": [True] * 5 + [False] * 5, "small_steps": [True] * 8 + [False] * 2}
    )
    assert learned(events, ev).helpful_approach == "small_steps"


def test_helpful_approach_no_data_below_min_samples() -> None:
    events, ev = _approach_journal({"small_steps": [True] * 9})
    assert learned(events, ev).helpful_approach is None


def test_help_or_other_tier_is_not_an_independent_success() -> None:
    events, ev = _approach_journal({"example_first": [True] * 10, "small_steps": [True] * 10})
    # The first ten attempts (example_first) were correct only with a hint: not independent.
    ev = [evidence(e.source_seq, assist="hint") if i < 10 else e for i, e in enumerate(ev)]
    assert learned(events, ev).helpful_approach == "small_steps"
    # Attempts of tier 2 are not T1: no samples at all.
    wrong_tier = [evidence(e.source_seq, tier=2) for e in ev]
    assert learned(events, wrong_tier).helpful_approach is None


def test_attempt_belongs_to_the_nearest_turn_of_the_skill() -> None:
    # Two turns aimed at the skill, then one attempt: only the second turn gets the sample.
    events = [turn(1, "small_steps", skills=(SKILL,)), turn(2, "attempt_first", skills=(SKILL,))]
    inputs = collect_profile_inputs(events, [evidence(3)])
    params = ProfileParams(profile_min_samples=1)
    assert fold_profile(inputs, params).helpful_approach == "attempt_first"


# --- branches and reasoning ------------------------------------------------------------------


def test_branch_rate_and_reasoning_share_with_thresholds() -> None:
    turns = [turn(i, branch="corrective" if i <= 3 else None) for i in range(1, 11)]
    answers = [answer(100 + i, text="because" if i < 5 else None) for i in range(10)]
    fold = learned(turns + answers, [])
    assert fold.branch_rate == 0.3
    assert fold.reasoning_share == 0.5
    few = learned(turns[:9] + answers[:9], [])
    assert few.branch_rate is None and few.reasoning_share is None


# --- preferences, manner, node props ---------------------------------------------------------


def test_prefs_and_manner_latest_wins_and_props_drop_no_data() -> None:
    events = [
        _event(1, "preference.set", {"key": "language", "value": "ru"}),
        _event(2, "preference.set", {"key": "answer_length", "value": "short"}),
        _event(3, "preference.set", {"key": "language", "value": "en"}),
    ]
    inputs = collect_profile_inputs(events)
    props, unset = profile_node_props(inputs, PARAMS)
    assert props == {"prefAnswerLength": "short", "prefLanguage": "en"}
    assert set(unset) == {
        "learnedHelpfulApproach",
        "learnedConfidenceBrier",
        "learnedConfidenceBias",
        "learnedBranchRate",
        "learnedReasoningShare",
    }
    assert pref_key("quiet_canvas") == "prefQuietCanvas"


# --- replay ----------------------------------------------------------------------------------


def _random_journal(rng: random.Random) -> tuple[list[StoredEvent], list[EvidenceSample]]:
    events: list[StoredEvent] = []
    ev: list[EvidenceSample] = []
    seq = 0
    approaches = ["example_first", "attempt_first", "small_steps"]
    for _ in range(40):
        seq += 1
        events.append(
            turn(
                seq,
                rng.choice(approaches),
                rng.choice([None, "corrective", "curiosity"]),
                (SKILL,),
            )
        )
        seq += 1
        events.append(answer(seq, rng.choice([None, "why"]), rng.choice([None, 1, 2, 3, 4])))
        ev.append(
            evidence(seq, rng.choice(["correct", "incorrect", "partial"]), gate=rng.random() > 0.2)
        )
    return events, ev


def test_replay_gives_the_same_profile() -> None:
    """Т-§9.5, Т-§2.5: the learned part is the same on rebuild — whatever the arrival order of
    events and evidence, and for a journal folded in one pass or batch by batch."""
    rng = random.Random(7)
    events, ev = _random_journal(rng)
    expected = profile_node_props(collect_profile_inputs(events, ev), PARAMS)
    assert expected[0]  # not an empty comparison
    for _ in range(5):
        shuffled_events, shuffled_ev = events[:], ev[:]
        rng.shuffle(shuffled_events)
        rng.shuffle(shuffled_ev)
        assert (
            profile_node_props(collect_profile_inputs(shuffled_events, shuffled_ev), PARAMS)
            == expected
        )
    # Batch by batch: the projector re-folds the whole journal of the learner each time and the
    # last batch ends at the same value as a rebuild.
    last = None
    for end in range(10, len(events) + 10, 10):
        cut = events[:end]
        seen = {e.seq for e in cut}
        last = profile_node_props(
            collect_profile_inputs(cut, [e for e in ev if e.source_seq in seen]), PARAMS
        )
    assert last == expected
