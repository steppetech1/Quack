"""reconcile_prior — memory-architecture §4.8."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.config import KnowledgeParams
from app.knowledge.reconcile import reconcile_prior
from app.schemas.knowledge import (
    AreaOut,
    KnowledgeStateOut,
    SkillRef,
    SkillWeight,
)

pytestmark = pytest.mark.phase1

PARAMS = KnowledgeParams()
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _skill(skill_id: str) -> SkillRef:
    return SkillRef(
        id=skill_id,
        name=skill_id,
        description="...",
        exam_ids=["SAT_MATH"],  # type: ignore[list-item]
        effort_h=4.0,
        base_half_life_h=None,
    )


def _weight(
    skill_id: str, weight: float = 3.0, area: str = "area.sat.algebra"
) -> SkillWeight:
    return SkillWeight(skill=_skill(skill_id), area_id=area, weight=weight)


def _area(area_id: str = "area.sat.algebra", share: float = 1.0) -> AreaOut:
    return AreaOut(id=area_id, name=area_id, score_share=share)


# --- self_assessment ---


def test_self_assessment_high_level_075():
    skills = [_weight("s1"), _weight("s2")]
    areas = [_area()]
    pairs = reconcile_prior(
        "academics.self_assessment",
        {"area.sat.algebra": 5},
        skills,
        areas,
        {},
        PARAMS,
        NOW,
    )
    assert len(pairs) == 2
    for ev, st in pairs:
        assert ev.kind == "self_report"
        assert ev.tier == 3
        assert st.p_at_obs == pytest.approx(0.75)
        assert st.confidence == pytest.approx(0.15)
        assert st.half_life_h == pytest.approx(PARAMS.h0_prior)


def test_self_assessment_medium_level_05():
    skills = [_weight("s1")]
    pairs = reconcile_prior(
        "academics.self_assessment",
        {"area.sat.algebra": 3},
        skills,
        [_area()],
        {},
        PARAMS,
        NOW,
    )
    assert pairs[0][1].p_at_obs == pytest.approx(0.5)


def test_self_assessment_low_level_03():
    skills = [_weight("s1")]
    pairs = reconcile_prior(
        "academics.self_assessment",
        {"area.sat.algebra": 2},
        skills,
        [_area()],
        {},
        PARAMS,
        NOW,
    )
    assert pairs[0][1].p_at_obs == pytest.approx(0.3)


def test_self_assessment_does_not_override_strong_state():
    existing = KnowledgeStateOut(
        skill_id="s1",
        exam_id="SAT_MATH",
        p_recall=0.9,
        p_at_obs=0.9,
        half_life_h=24.0,
        confidence=0.8,  # >= c_vis
        evidence_mass=1.0,
        n_correct=1,
        n_incorrect=0,
        n_partial=0,
        has_strong=True,
        last_observed_at=NOW,
        created_at=NOW,
    )
    skills = [_weight("s1")]
    pairs = reconcile_prior(
        "academics.self_assessment",
        {"area.sat.algebra": 2},
        skills,
        [_area()],
        {"s1": existing},
        PARAMS,
        NOW,
    )
    assert pairs == []


# --- trial score ---


def test_trial_score_distributes_by_total_weight():
    # total_weight = 6, value = 3 → p = 0.5
    skills = [_weight("s1", 3.0), _weight("s2", 3.0)]
    pairs = reconcile_prior(
        "academics.ent_trial_score",
        3.0,
        skills,
        [_area()],
        {},
        PARAMS,
        NOW,
    )
    assert len(pairs) == 2
    for _, st in pairs:
        assert st.p_at_obs == pytest.approx(0.5)


def test_sat_score_same_as_ent():
    skills = [_weight("s1", 4.0)]
    pairs = reconcile_prior(
        "academics.sat_score",
        2.0,
        skills,
        [_area()],
        {},
        PARAMS,
        NOW,
    )
    assert pairs[0][1].p_at_obs == pytest.approx(0.5)


def test_trial_score_does_not_override_strong_state():
    existing = KnowledgeStateOut(
        skill_id="s1",
        exam_id="SAT_MATH",
        p_recall=0.9,
        p_at_obs=0.9,
        half_life_h=24.0,
        confidence=0.8,
        evidence_mass=1.0,
        n_correct=1,
        n_incorrect=0,
        n_partial=0,
        has_strong=True,
        last_observed_at=NOW,
        created_at=NOW,
    )
    skills = [_weight("s1")]
    pairs = reconcile_prior(
        "academics.ent_trial_score",
        3.0,
        skills,
        [_area()],
        {"s1": existing},
        PARAMS,
        NOW,
    )
    assert pairs == []


# --- ignored fields ---


def test_unknown_field_returns_empty():
    skills = [_weight("s1")]
    pairs = reconcile_prior(
        "pace.hours_per_week", 5, skills, [_area()], {}, PARAMS, NOW
    )
    assert pairs == []


def test_invalid_value_type_returns_empty():
    skills = [_weight("s1")]
    pairs = reconcile_prior(
        "academics.self_assessment",
        "not a dict",
        skills,
        [_area()],
        {},
        PARAMS,
        NOW,
    )
    assert pairs == []


def test_trial_score_out_of_scale_writes_no_prior():
    """«Пробный ЕНТ 95» — это сумма по пяти предметам, а не математика из 50.

    Обрезка до 1.0 заявляла бы идеальное знание каждого навыка и выносила их
    из плана; молчание честнее выдуманного приора.
    """
    skills = [_weight("s1", 3.0), _weight("s2", 3.0)]
    assert (
        reconcile_prior(
            "academics.ent_trial_score", 95.0, skills, [_area()], {}, PARAMS, NOW
        )
        == []
    )
