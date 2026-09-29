"""build_queue — memory-architecture §10.1.

Numbers from 20-B1-phase2.md §8 (tests/sets/test_queue.py).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import KnowledgeParams
from app.schemas.knowledge import (
    KnowledgeStateOut,
    Prerequisite,
    RootCauseOut,
    SkillRef,
    SkillWeight,
)
from app.sets.queue import QueueItem, build_queue

pytestmark = pytest.mark.phase1

PARAMS = KnowledgeParams()
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def _skill(skill_id: str, *, exam_id: str = "SAT_MATH") -> SkillRef:
    return SkillRef(
        id=skill_id,
        name=skill_id,
        description="...",
        exam_ids=[exam_id],  # type: ignore[list-item]
        effort_h=4.0,
        base_half_life_h=None,
    )


def _weight(
    skill_id: str, weight: float = 3.0, *, area: str = "area.sat.algebra"
) -> SkillWeight:
    return SkillWeight(skill=_skill(skill_id), area_id=area, weight=weight)


def _state(
    skill_id: str,
    *,
    p_recall: float = 0.5,
    confidence: float = 0.8,
    exam_id: str = "SAT_MATH",
) -> KnowledgeStateOut:
    return KnowledgeStateOut(
        skill_id=skill_id,
        exam_id=exam_id,  # type: ignore[arg-type]
        p_recall=p_recall,
        p_at_obs=p_recall,
        half_life_h=24.0,
        confidence=confidence,
        evidence_mass=1.0,
        n_correct=1,
        n_incorrect=0,
        n_partial=0,
        has_strong=True,
        last_observed_at=NOW - timedelta(days=1),
        created_at=NOW - timedelta(days=2),
    )


def _prereq(skill_id: str, strength: float = 0.9) -> Prerequisite:
    return Prerequisite(skill_id=skill_id, strength=strength, depth=1)


# --- need / urgency ---


def test_need_formula_two_skills():
    states = [
        _state("a", p_recall=0.5),
        _state("b", p_recall=0.8),
    ]
    weights = [_weight("a", 3.0), _weight("b", 3.0)]
    queue = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    # need(a) = 3 * (0.9 - 0.5) * urgency
    # need(b) = 3 * (0.9 - 0.8) * urgency
    assert queue[0].skill_id == "a"
    assert queue[1].skill_id == "b"


def test_urgency_grows_when_test_close():
    states = [_state("a", p_recall=0.5)]
    weights = [_weight("a", 3.0)]
    q_far = build_queue(
        states,
        weights,
        [],
        days_to_test=60,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    q_near = build_queue(
        states,
        weights,
        [],
        days_to_test=10,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    assert q_near[0].urgency > q_far[0].urgency
    assert q_far[0].urgency == pytest.approx(1.0)  # (60-60)/60 = 0


def test_no_state_treated_as_prior():
    # нет состояния — p_recall приора (0.5), conf=0
    states: list[KnowledgeStateOut] = []
    weights = [_weight("a", 3.0)]
    queue = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    assert len(queue) == 1
    assert queue[0].gap == pytest.approx(0.9 - 0.5)


# --- prerequisites order ---


def test_prereq_with_gap_comes_first():
    # b требует a; у a gap, у b gap — a раньше
    states = [
        _state("a", p_recall=0.4),  # gap 0.5
        _state("b", p_recall=0.3),  # gap 0.6 → без топа был бы первым
    ]
    weights = [_weight("a", 3.0), _weight("b", 3.0)]
    prereqs = [Prerequisite(skill_id="a", strength=0.9, depth=1)]  # кто-то требует a
    # но b зависит от a: сделаем связь a < b через prereq
    prereqs = [
        Prerequisite(skill_id="a", strength=0.9, depth=1),
        Prerequisite(skill_id="b", strength=0.9, depth=1),
    ]
    queue = build_queue(
        states,
        weights,
        prereqs,
        days_to_test=30,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    # prerequisites — плоский список «кого требуют»; порядок a→b не задан.
    # Проверяем только состав.
    assert {q.skill_id for q in queue} == {"a", "b"}


def test_weak_prereq_becomes_check():
    # навык с conf < c_vis и gap > 0 → is_check=True
    states = [_state("a", p_recall=0.3, confidence=0.1)]
    weights = [_weight("a", 3.0)]
    queue = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    assert queue[0].is_check is True


# --- closed skills ---


def test_closed_skill_not_in_queue():
    # p_recall >= p_target и conf >= c_close → closed
    states = [_state("a", p_recall=0.95, confidence=0.9)]
    weights = [_weight("a", 3.0)]
    queue = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    assert queue == []


# --- root boost ---


def test_root_boost_multiplies_need():
    states = [_state("a", p_recall=0.5), _state("b", p_recall=0.5)]
    weights = [_weight("a", 3.0), _weight("b", 3.0)]
    roots = [
        RootCauseOut(
            from_skill_id="x",
            root_skill_id="a",
            confidence=1.5,
            source="diagnostic",
            created_at=NOW - timedelta(days=1),
        )
    ]
    q_no_roots = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    q_with_roots = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=roots,
        params=PARAMS,
    )
    # a — с root boost, его need выше
    need_a_no = next(q.need for q in q_no_roots if q.skill_id == "a")
    need_a_yes = next(q.need for q in q_with_roots if q.skill_id == "a")
    need_b_yes = next(q.need for q in q_with_roots if q.skill_id == "b")
    assert need_a_yes > need_b_yes
    assert need_a_yes == pytest.approx(need_a_no * PARAMS.root_boost)


def test_is_root_marks_skill():
    states = [_state("a", p_recall=0.5)]
    weights = [_weight("a", 3.0)]
    roots = [
        RootCauseOut(
            from_skill_id="x",
            root_skill_id="a",
            confidence=1.5,
            source="diagnostic",
            created_at=NOW - timedelta(days=1),
        )
    ]
    queue = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=roots,
        params=PARAMS,
    )
    assert queue[0].is_root is True


# --- general ---


def test_returns_list_of_queue_items():
    states = [_state("a", p_recall=0.5)]
    weights = [_weight("a", 3.0)]
    queue = build_queue(
        states,
        weights,
        [],
        days_to_test=30,
        p_target=0.9,
        root_causes=[],
        params=PARAMS,
    )
    assert isinstance(queue, list)
    assert isinstance(queue[0], QueueItem)


# --- низкая цель и непроверенные навыки ---


def test_unseen_skill_survives_a_target_below_the_prior():
    """Программа с низким порогом не должна оставлять ученика без плана.

    ЕНТ-порог 12 из 50 даёт p_target 0.24 — ниже приора 0.5. Навык, который мы
    ни разу не видели, обязан остаться в очереди как проверка: приор — это не
    знание. Иначе «Подготовка» пуста у человека, который ещё ничего не решал.
    """
    weights = [_weight("a", 3.0), _weight("b", 3.0)]
    queue = build_queue(
        [],
        weights,
        [],
        days_to_test=120,
        p_target=0.24,
        root_causes=[],
        params=PARAMS,
    )
    assert [item.skill_id for item in queue] == ["a", "b"]
    assert all(item.is_check for item in queue)
    assert all(item.gap > 0 and item.need > 0 for item in queue)


def test_a_seen_skill_above_the_target_stays_out():
    """Обратная сторона: там, где знание настоящее, учить действительно нечего."""
    states = [_state("a", p_recall=0.9, confidence=0.8)]
    queue = build_queue(
        states,
        [_weight("a", 3.0)],
        [],
        days_to_test=120,
        p_target=0.24,
        root_causes=[],
        params=PARAMS,
    )
    assert queue == []
