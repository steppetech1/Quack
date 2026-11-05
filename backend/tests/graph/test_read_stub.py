"""ТЗ Ф4 WP6.4, seam S4: signatures of `quack.graph.read` and the double with fixed data."""

from __future__ import annotations

import asyncio
from uuid import UUID

import pytest
from pydantic import ValidationError

from quack.graph.read import EvidenceFact, GraphReader, LevelFact, MisconceptionFact, SliceFocus
from tests.support import graph_read as fx


def _reader(lag_degraded: bool = False) -> GraphReader:
    return fx.FixedGraphReader(lag_degraded=lag_degraded)


def test_slice_has_level_states_for_second_opinion() -> None:
    sl = asyncio.run(_reader().slice_for_turn(fx.LEARNER, UUID(int=1), SliceFocus()))
    by_skill = {s.skill_id: s for s in sl.skills}
    assert by_skill[fx.SKILL_PROVEN].levels[0].proven_by_direct
    assert by_skill[fx.SKILL_FAILED].levels[0].last_t1_failed
    # law 8: unchecked is not failed
    assert by_skill[fx.SKILL_QUIET].levels[0].status == "not_tested"
    assert sl.projected_seq == fx.PROJECTED_SEQ
    assert not sl.lag_degraded


def test_slice_can_be_lagging() -> None:
    sl = asyncio.run(
        _reader(lag_degraded=True).slice_for_turn(fx.LEARNER, UUID(int=1), SliceFocus())
    )
    assert sl.lag_degraded


def test_slice_focus_narrows_skills() -> None:
    focus = SliceFocus(skill_ids=[fx.SKILL_FAILED])
    sl = asyncio.run(_reader().slice_for_turn(fx.LEARNER, UUID(int=1), focus))
    assert [s.skill_id for s in sl.skills] == [fx.SKILL_FAILED]


def test_frontier_is_lowest_open_level_of_the_set() -> None:
    f = asyncio.run(_reader().frontier(fx.SKILL_PROVEN, [4, 3]))
    assert f.level == 4
    assert asyncio.run(_reader().frontier(fx.SKILL_PROVEN, [3])).level is None


def test_readiness_counts_topics() -> None:
    r = asyncio.run(_reader().topic_readiness(fx.HUB))
    assert (r.ready_count, r.total) == (1, 2)


def test_review_candidates_respect_max_items() -> None:
    from quack.graph.read import ReviewScope

    found = asyncio.run(
        _reader().review_candidates(fx.LEARNER, fx.NOW_MS, ReviewScope(max_items=1))
    )
    assert [c.skill_id for c in found] == [fx.SKILL_PROVEN]


def test_prep_context_and_state_at() -> None:
    prep = asyncio.run(_reader().prep_context(fx.HUB, fx.NOW_MS))
    assert prep.days_to_deadline == 5 and prep.candidates
    at = asyncio.run(_reader().state_at(fx.LEARNER, 50, [fx.SKILL_PROVEN]))
    assert at.seq == 50 and at.skills[0].skill_id == fx.SKILL_PROVEN


def test_models_reject_unknown_fields_and_bad_values() -> None:
    with pytest.raises(ValidationError):
        LevelFact.model_validate({"level": 3, "status": "proven", "weight": 0.5})
    with pytest.raises(ValidationError):
        LevelFact.model_validate({"level": 7, "status": "proven"})
    with pytest.raises(ValidationError):
        MisconceptionFact(
            misconception_id=UUID(int=1),
            skill_id=UUID(int=2),
            status="suspected",
            statement="adds denominators",
            quotes=["a", "b", "c"],
        )
    with pytest.raises(ValidationError):  # Т-§9.3: a misconception always has its statement
        MisconceptionFact.model_validate(
            {"misconception_id": UUID(int=1), "skill_id": UUID(int=2), "status": "suspected"}
        )


def test_misconception_carries_its_statement() -> None:
    """D-B-145: `check.model` shows the model `M1. <statement>`."""
    sl = asyncio.run(_reader().slice_for_turn(fx.LEARNER, UUID(int=1), SliceFocus()))
    (found,) = sl.misconceptions
    assert found.misconception_id == fx.MISCONCEPTION
    assert found.statement == fx.MISCONCEPTION_STATEMENT


def test_requires_carry_confidence_for_the_guard_rule() -> None:
    sl = asyncio.run(_reader().slice_for_turn(fx.LEARNER, UUID(int=1), SliceFocus()))
    failed = next(s for s in sl.skills if s.skill_id == fx.SKILL_FAILED)
    assert [(link.confidence, link.has_evidence) for link in failed.requires] == [
        (0.9, True),
        (0.4, False),
    ]


def test_memory_picture_facts_of_wp14_2() -> None:
    """D-B-157 (D-A-126): gap type with its confidence (`Т-§8.4`), the last memory lapse —
    «забыл» (`Т-§8.2` п.3), the conditions a misconception shows in (`Т-§9.3`)."""
    sl = asyncio.run(_reader().slice_for_turn(fx.LEARNER, UUID(int=1), SliceFocus()))
    failed = next(s for s in sl.skills if s.skill_id == fx.SKILL_FAILED)
    assert failed.gap_type == "execution" and failed.gap_type_confidence == 0.8
    assert failed.last_lapse_at_ms == fx.LAPSE_AT_MS
    quiet = next(s for s in sl.skills if s.skill_id == fx.SKILL_QUIET)
    assert quiet.gap_type_confidence is None and quiet.last_lapse_at_ms is None  # law 8
    (misconception,) = sl.misconceptions
    assert misconception.triggers == list(fx.MISCONCEPTION_TRIGGERS)


def test_skill_history_says_what_help_was_given() -> None:
    """D-B-163 (D-A-127): `skill_history` gives the help of each evidence — none, a hint with
    its step 1–3 or a shown solution (`Т-§10.4`; Evidence `assistance`, `hintStep`, `Т-§3.4.3`)."""
    (hinted,) = asyncio.run(_reader().skill_history(fx.SKILL_FAILED))
    assert hinted.assistance == "hint" and hinted.hint_step == fx.HINT_STEP
    (plain,) = asyncio.run(_reader().skill_history(fx.SKILL_PROVEN))
    assert plain.assistance == "none" and plain.hint_step is None  # law 8
    shown = hinted.model_dump() | {"assistance": "shown_solution", "hint_step": None}
    assert EvidenceFact.model_validate(shown).assistance == "shown_solution"
    with pytest.raises(ValidationError):  # Т-§7.6: hint steps are 1–3
        EvidenceFact.model_validate(hinted.model_dump() | {"hint_step": 4})


def test_extraction_candidates_of_wp15() -> None:
    """WP15 п.2, п.6 (D-A-135): skills of the session's topics plus the nearest by the episode
    vector, up to `k`; for re-extraction only the skills of a topic or the given skills; the
    known misconceptions of the candidates (`Т-§7.6`)."""
    reader, session = _reader(), UUID(int=1)
    found = asyncio.run(reader.extraction_candidates(fx.LEARNER, session, [0.1, 0.2], k=15))
    assert {c.skill_id for c in found.skills} == {fx.SKILL_PROVEN, fx.SKILL_FAILED, fx.SKILL_QUIET}
    assert all(c.name and c.topic_id for c in found.skills)
    assert [m.misconception_id for m in found.misconceptions] == [fx.MISCONCEPTION]
    by_topic = asyncio.run(
        reader.extraction_candidates(fx.LEARNER, session, None, k=15, topic_id=fx.TOPIC_OPEN)
    )
    assert {c.skill_id for c in by_topic.skills} == {fx.SKILL_FAILED, fx.SKILL_QUIET}
    only = asyncio.run(
        reader.extraction_candidates(fx.LEARNER, session, None, k=15, skill_ids=[fx.SKILL_QUIET])
    )
    assert [c.skill_id for c in only.skills] == [fx.SKILL_QUIET]
    assert only.misconceptions == []  # the misconception is about another skill
    assert (
        len(asyncio.run(reader.extraction_candidates(fx.LEARNER, session, None, k=1)).skills) == 1
    )


def test_slice_skills_carry_names() -> None:
    sl = asyncio.run(_reader().slice_for_turn(fx.LEARNER, UUID(int=1), SliceFocus()))
    failed = next(s for s in sl.skills if s.skill_id == fx.SKILL_FAILED)
    assert (failed.name, failed.topic_name) == ("common denominator", "reducing fractions")


def test_domain_goals_give_topics_with_required_levels() -> None:
    (goal,) = asyncio.run(_reader().domain_goals(fx.DOMAIN))
    assert goal.hub_id == fx.HUB and goal.deadline_at_ms == fx.GOAL_DEADLINE_MS
    assert [t.topic_id for t in goal.topics] == [fx.TOPIC_READY, fx.TOPIC_OPEN]
    assert all(t.name and t.required_levels for t in goal.topics)


def test_profile_separates_no_data_from_a_value() -> None:
    p = asyncio.run(_reader().profile(fx.LEARNER))
    assert p.prefs["language"] == "ru" and p.manner
    assert p.learned_branch_rate is None  # «no data», not zero
    assert p.learned_confidence_bias == "over"


def test_topic_memory_skips_unknown_topics() -> None:
    found = asyncio.run(_reader().topic_memory([fx.TOPIC_OPEN, UUID(int=999)]))
    assert [m.topic_id for m in found] == [fx.TOPIC_OPEN]
    assert {s.skill_id for s in found[0].skills} == {fx.SKILL_FAILED, fx.SKILL_QUIET}
    assert [m.misconception_id for m in found[0].misconceptions] == [fx.MISCONCEPTION]


def test_misconception_detail_has_every_manifestation() -> None:
    detail = asyncio.run(_reader().misconception_detail(fx.MISCONCEPTION))
    assert detail is not None
    assert (detail.status, detail.skill_name) == ("confirmed", "common denominator")
    assert [m.task_id is None for m in detail.manifestations] == [True, False]
    assert asyncio.run(_reader().misconception_detail(UUID(int=999))) is None


def test_factory_builds_the_neo4j_reader() -> None:
    from quack.config import Settings
    from quack.graph.read import create_graph_reader
    from quack.graph.read.neo4j_reader import Neo4jGraphReader

    reader = create_graph_reader(None, Settings(), learner_id=fx.LEARNER)  # type: ignore[arg-type]
    assert isinstance(reader, Neo4jGraphReader)
