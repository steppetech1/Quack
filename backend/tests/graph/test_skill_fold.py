"""ТЗ Ф4 §8: folds of `rules@3` — skill (T-F*), misconception (T-MS1), topic (T-TH1–TH3)."""

from __future__ import annotations

from typing import Any

from quack.graph.projection.skill_fold import FoldEvidence, FoldExposure, FoldParams, fold_skill

P = FoldParams()
H = 3_600_000
DAY = 24 * H


def _ev(n: int, outcome: str = "correct", *, at: int | None = None, **kw: Any) -> FoldEvidence:
    fields: dict[str, Any] = {
        "id": f"e{n}",
        "at_ms": at if at is not None else n * DAY,
        "seq": n,
        "level": 3,
        "outcome": outcome,
        "tier": 1,
        "verification": "code",
        "family_id": f"f{n}",
    } | kw
    return FoldEvidence(**fields)


def _status(evidence: list[FoldEvidence], level: int = 3) -> str:
    return str(fold_skill(evidence, [], P).skill[f"l{level}Status"])


def test_section_8_2_examples() -> None:
    """T-F1: the five examples of `Т-§8.2` with the starting values."""
    one = fold_skill([_ev(1)], [], P).skill
    assert (one["l3Status"], one["l3Basis"], one["l3SuccessW"]) == ("proven", "direct", 1.0)
    assert (one["l1Status"], one["l2Status"]) == ("implied", "unknown")  # 3 ⇒ 1, not 2

    assert _status([_ev(1, options=4)]) == "unknown"  # 0.75
    assert _status([_ev(1, options=4), _ev(2, options=4)]) == "proven"

    transfer: dict[str, Any] = {"derived_kind": "transfer"}
    assert _status([_ev(1, **transfer)]) == "unknown"  # 0.6
    two = fold_skill([_ev(1, **transfer), _ev(2, **transfer)], [], P).skill
    assert (two["l3Status"], two["l3Basis"]) == ("proven", "transfer")  # ≈ 0.96

    five = [_ev(n, at=n * H) for n in range(1, 6)]  # an hour apart: not a memory failure
    assert _status([*five, _ev(6, "incorrect", at=6 * H)]) == "unstable"
    assert _status([*five, _ev(6, "incorrect", at=6 * H), _ev(7, at=7 * H)]) == "proven"

    month = fold_skill([_ev(1), _ev(31, "incorrect")], [], P).skill
    assert month["l3Status"] == "proven"  # forgetting, not misunderstanding
    assert (month["retLapses"], month["retLastLapseAtMs"]) == (1, 31 * DAY)


def test_gates() -> None:
    """T-F2 (`Т-§7.3`): T1/T2 only, a model verdict only in a pair, T3 behind the gate, partial
    and a shown solution never open; levels 4–6 need novelty."""
    model: dict[str, Any] = {"verification": "model", "tier": 2}
    assert _status([_ev(1, **model)]) == "unknown"  # one model verdict — never alone
    assert _status([_ev(1, **model), _ev(2, **model)]) == "proven"  # another family
    assert (
        _status([_ev(1, **model), _ev(2, family_id="f1", verification="model", tier=2)])
        == "unknown"
    )
    assert _status([_ev(1, tier=3), _ev(2, tier=3), _ev(3, tier=3)]) == "unknown"  # T3 behind
    assert _status([_ev(1, "partial")]) == "unknown"
    assert _status([_ev(1, level=4), _ev(2, "incorrect", level=4, family_id="f1")], 4) == "proven"
    shown = fold_skill([_ev(1, assistance="shown_solution", task_max_level=4)], [], P).skill
    assert (shown["l3Status"], shown["needsConsolidation"], shown["consolidationLevel"]) == (
        "unknown",
        True,
        4,
    )
    assert (
        fold_skill([_ev(1, assistance="shown_solution"), _ev(2)], [], P).skill["needsConsolidation"]
        is False
    )


def test_fsrs_card_and_reviews() -> None:
    """T-F6: the card starts with the first success behind the gate; a review is only a delayed
    one on a proven level; the hot window is not a review; same input — same `retDueAtMs`."""
    first = fold_skill([_ev(1)], [], P).skill
    assert (first["retState"], first["retReviews"], first["retLastRecallAtMs"]) == (
        "learning",
        1,
        DAY,
    )
    due = first["retDueAtMs"]
    assert isinstance(due, int) and due > DAY
    hot = [FoldExposure(at_ms=3 * DAY - 60_000, seq=0, kind="example")]
    after_show = fold_skill([_ev(1), _ev(3)], hot, P)
    assert after_show.skill["retReviews"] == 1  # hot window: not a review
    assert after_show.evidence["e3"]["weight"] == 0.5
    review = fold_skill([_ev(1), _ev(3)], [], P).skill
    assert (review["retState"], review["retReviews"]) == ("review", 2)
    assert review == fold_skill([_ev(1), _ev(3)], [], P).skill
    near = fold_skill([_ev(1), _ev(2, at=DAY + H)], [], P).skill
    assert near["retReviews"] == 1  # not delayed: an hour later


def test_misconception_lifecycle_table() -> None:
    """Т-§9.2 (WP5.2): suspected → confirmed → watching → fixed; a new manifestation returns
    to confirmed; resistances of one family do not count twice; disputed and in_work."""
    from quack.graph.projection.misconception_fold import (
        FoldSign,
        MisconceptionParams,
        fold_misconception,
    )

    mp = MisconceptionParams()

    def sign(day: int, family: str, *, resists: bool = False, tier: int = 1) -> FoldSign:
        return FoldSign(day * DAY, day, tier, family, resists)

    def status(*signs: FoldSign, **kw: Any) -> str:
        return str(fold_misconception(signs, mp, **kw)["status"])

    shown = [sign(1, "a"), sign(2, "b", tier=3)]
    assert status(shown[0]) == "suspected"
    assert status(sign(1, "a", tier=3), sign(2, "b", tier=3)) == "suspected"  # no T1/T2
    assert status(*shown) == "confirmed"
    assert status(*shown, targeted_session="s") == "in_work"
    resist = [sign(3, "c", resists=True), sign(4, "c", resists=True)]
    assert status(*shown, *resist) == "confirmed"  # one family
    watching = [*shown, sign(3, "c", resists=True), sign(4, "d", resists=True)]
    state = fold_misconception(watching, mp)
    assert (state["status"], state["watchSinceMs"], state["occurrences"]) == (
        "watching",
        4 * DAY,
        2,
    )
    assert status(*watching, sign(6, "e", resists=True)) == "watching"  # 2 days
    assert status(*watching, sign(7, "e", resists=True)) == "fixed"
    assert status(*watching, sign(7, "e", resists=True), sign(8, "f")) == "confirmed"
    assert status(shown[0], sign(2, "c", resists=True), sign(3, "d", resists=True)) == "dismissed"
    assert status(*shown, disputed=True) == "disputed"


def test_display_state_priority() -> None:
    """T-TH2 (WP6.2, `Т-§8.6`): the eight rules by priority with the parameters of `Т-§22.2`;
    the target set from the hub with the nearest deadline; without a hub — level 3."""
    from quack.graph.projection.topic_fold import (
        TopicGoal,
        TopicParams,
        TopicSkill,
        fold_topic,
    )

    tp = TopicParams(control_type_levels={"no_goal": [3], "tasks": [3, 4]})
    active: dict[str, Any] = {"status": "active"}

    def skill(sid: str, *confirmed: tuple[str, str], **levels: str) -> TopicSkill:
        props: dict[str, Any] = {f"l{n}Status": "unknown" for n in range(1, 7)}
        props |= {f"l{n}Basis": "none" for n in range(1, 7)}
        for key, value in levels.items():
            props[key] = value
        props["retDueAtMs"] = 5 if sid == "a" else 0
        return TopicSkill(sid, props, confirmed)

    def state(*skills: TopicSkill, goals: list[TopicGoal] | None = None) -> tuple[Any, ...]:
        out = fold_topic(active, skills, goals or [], tp)
        return out["displayState"], out["displayReason"], out["displayLevel"]

    proven = {"l3Status": "proven", "l3Basis": "direct"}
    assert state(skill("a", ("m1", "x")), skill("b", ("m2", ""))) == ("confused_with", "m1", 0)
    assert state(skill("a", ("m2", "")), skill("b", l3Status="failed")) == (
        "persistent_error",
        "m2",
        0,
    )
    assert state(skill("a", l3Status="unstable"), skill("b", l3Status="failed")) == (
        "struggling",
        "b",
        3,
    )
    assert state(skill("a", l3Status="unstable")) == ("inconsistent", "a", 3)
    assert state(skill("a", **proven)) == ("solved_independently", "", 0)
    goal = [TopicGoal(10, [3, 4]), TopicGoal(20, [2])]
    assert state(skill("a", **proven), goals=goal) == ("in_progress", "", 0)  # 4 not tested
    transfer = {"l3Status": "proven", "l3Basis": "transfer"}
    assert state(skill("a", **transfer))[0] == "known_from_other_domain"
    assert state(skill("a")) == ("not_tested", "", 0)
    out = fold_topic({"status": "proposed"}, [skill("a")], [], tp)
    assert (out["displayState"], out["visibility"], out["nearestDueAtMs"]) == ("none", "pale", 5)
    touched = skill("a")
    touched.props["expCount"] = 1
    assert fold_topic({"status": "proposed"}, [touched], [], tp)["status"] == "active"


def test_weights_and_novelty_in_fold() -> None:
    """T-F3 (`Т-§7.4`, `Т-§5.4`): weight by tier, guessing, hot window and hint; novelty and
    `delayed` from the evidence before it in time — even when it arrived later."""
    shows = [FoldExposure(at_ms=2 * DAY - 60_000, seq=0, kind="example")]
    late = _ev(1, at=2 * DAY, tier=2, options=4, hint_step=1, verification="model")
    fold = fold_skill([_ev(9, at=DAY), late], shows, P)  # e1 came later in the log
    assert fold.evidence["e1"]["weight"] == round(0.8 * 0.75 * 0.5 * 0.75, 6)
    assert fold.evidence["e1"]["delayed"] is False  # 1 minute after the exposure
    other = fold_skill([_ev(1), _ev(2, family_id="f1", task_domain_id="d2")], [], P)
    assert other.evidence["e2"]["novelty"] == "same"
    cross = fold_skill([_ev(1, task_domain_id="d1"), _ev(2, task_domain_id="d2")], [], P,
                       skill_domain="d1")  # fmt: skip
    assert (cross.evidence["e1"]["novelty"], cross.evidence["e2"]["novelty"]) == (
        "new",
        "cross_domain",
    )
    assert cross.evidence["e2"]["delayed"] is True  # a day after the last practice


def test_implications_and_direct_over_implied() -> None:
    """T-F4 (`Т-§8.2` п.4): 4 ⇒ 3, 1; level 2 follows from nothing; failed by its own evidence
    but implied by a proven higher level — unstable."""
    four = fold_skill([_ev(1, level=4)], [], P).skill
    assert [four[f"l{n}Status"] for n in range(1, 7)] == [
        "implied", "unknown", "implied", "proven", "unknown", "unknown",
    ]  # fmt: skip
    assert four["l3Basis"] == "implied"
    mixed = [_ev(1, level=4), _ev(2, "incorrect", at=DAY + H), _ev(3, "incorrect", at=DAY + 2 * H)]
    assert fold_skill(mixed, [], P).skill["l3Status"] == "unstable"


def test_delayed_fail_on_proven_is_memory() -> None:
    """T-F5 (`Т-§8.2` п.3): a delayed failure on a proven level — F × 0.25, earlier weights kept,
    an `Again`; after an exposure the next failure counts in full."""
    memory = fold_skill([_ev(1), _ev(31, "incorrect")], [], P).skill
    assert (memory["l3SuccessW"], memory["l3FailW"]) == (1.0, 0.25)
    again = [_ev(1), _ev(31, "incorrect"), _ev(40, "incorrect")]
    assert fold_skill(again, [], P).skill["l3FailW"] == 0.5  # no reminder: forgetting again
    shown = [FoldExposure(at_ms=35 * DAY, seq=0, kind="explained")]
    after = fold_skill(again, shown, P).skill
    assert (after["l3FailW"], after["l3Status"]) == (1.25, "failed")  # after a reminder


def test_exposure_and_signals() -> None:
    """T-F8 (`Т-§8.4`): exposure counters, gap type vote, last practice, consolidation."""
    shows = [FoldExposure(DAY, 0, "explained"), FoldExposure(2 * DAY, 1, "example")]
    wrong = [
        _ev(1, "incorrect", gap_type="execution"),
        _ev(2, "incorrect", gap_type="execution", level=0),
        _ev(3, "incorrect", gap_type="no_idea", tier=3),
    ]
    s = fold_skill(wrong, shows, P).skill
    assert (s["expCount"], s["expLastExplainedAtMs"], s["expLastExampleAtMs"]) == (2, DAY, 2 * DAY)
    assert (s["gapType"], s["lastPracticeAtMs"]) == ("execution", 3 * DAY)
    confidence = s["gapTypeConfidence"]
    assert isinstance(confidence, float) and 0.5 < confidence < 1
    bypass = fold_skill([_ev(1, assistance="shown_solution"), _ev(2, level=2)], [], P).skill
    assert (bypass["needsConsolidation"], bypass["bypassCount"]) == (True, 1)  # 2 < 3


def test_fold_is_pure() -> None:
    """T-F9 (`Т-§8.1`, `Т-§2.5`): the same input in any order gives the same output."""
    evidence = [_ev(n, "incorrect" if n % 3 == 0 else "correct", level=1 + n % 4) for n in range(9)]
    shows = [FoldExposure(n * DAY, n, "example") for n in range(0, 9, 2)]
    first = fold_skill(evidence, shows, P)
    again = fold_skill(list(reversed(evidence)), list(reversed(shows)), P)
    assert (first.skill, first.evidence) == (again.skill, again.evidence)


def test_topic_status_visibility() -> None:
    """T-TH1 (`Т-§4.7`, `Т-§4.11`): visibility by status; proposed becomes active on the first
    evidence or exposure; a proposed topic claimed known is hidden; only active get a state."""
    from quack.graph.projection.topic_fold import TopicParams, TopicSkill, fold_topic

    idle = TopicSkill("s", {"lastPracticeAtMs": 0, "expCount": 0})
    used = TopicSkill("s", {"lastPracticeAtMs": 5, "expCount": 0})
    cases = [
        ({"status": "proposed"}, idle, ("proposed", "pale", "none")),
        ({"status": "proposed", "knownClaimedAtMs": 1}, idle, ("proposed", "hidden", "none")),
        ({"status": "proposed"}, used, ("active", "shown", "not_tested")),
        ({"status": "hidden"}, used, ("hidden", "hidden", "none")),
        ({"status": "off_domain"}, used, ("off_domain", "shown", "none")),
    ]
    for topic, skill, expected in cases:
        out = fold_topic(dict(topic), [skill], [], TopicParams())
        assert (out["status"], out["visibility"], out["displayState"]) == expected, topic


def test_hub_required_levels_default() -> None:
    """T-TH3 (`Т-§8.5`, WP6.3): `INCLUDES` without levels takes them from `controlType`; the hub
    with the nearest deadline sets the target; the hub of the whole domain counts too."""
    from quack.graph.projection.phase_b import fold_dirty_topics
    from quack.graph.projection.topic_fold import TopicParams
    from quack.graph.projection.types import NodeRecord, RelRecord, SubGraph
    from quack.graph.projection.view import BatchSlice

    skill: dict[str, Any] = {f"l{n}Status": "unknown" for n in range(1, 7)} | {"l3Status": "proven",
                                                                 "l3Basis": "direct"}  # fmt: skip
    graph = SubGraph(
        nodes=[
            NodeRecord("Topic", "t", {"status": "active", "domainId": "d"}),
            NodeRecord("Skill", "s", skill),
            NodeRecord("Domain", "d", {}),
            NodeRecord("Hub", "exam", {"controlType": "tasks", "deadlineAtMs": 10}),
            NodeRecord(
                "Hub",
                "all",
                {"kind": "domain_all", "controlType": "oral_theory", "deadlineAtMs": 5},
            ),
        ],
        rels=[
            RelRecord("Topic", "t", "COVERS", "Skill", "s", {}),
            RelRecord("Hub", "exam", "INCLUDES", "Topic", "t", {"requiredLevels": []}),
            RelRecord("Domain", "d", "HAS_HUB", "Hub", "all", {}),
        ],
    )
    levels = {"tasks": [3, 4], "oral_theory": [2, 6], "no_goal": [3]}
    batch = BatchSlice("l", graph)
    fold_dirty_topics(batch, {"t"}, TopicParams(control_type_levels=levels))
    topic = batch.node("Topic", "t") or {}
    assert (topic["displayState"], topic["displayLevel"]) == ("not_tested", 0)  # 2, 6 nearest
    graph.nodes[4] = NodeRecord("Hub", "all", {"kind": "domain_all", "deadlineAtMs": 0})
    batch = BatchSlice("l", graph)
    fold_dirty_topics(batch, {"t"}, TopicParams(control_type_levels=levels))
    assert (batch.node("Topic", "t") or {})["displayState"] == "in_progress"  # 3 of 3, 4
