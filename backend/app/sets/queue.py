"""Queue of skills to study — memory-architecture-quack.md §10.1.

Pure function: rank skills by need × urgency × root boost.
Source: 20-B1-phase2.md §3.1, 00-contracts-phase2.md §7.1.
"""

from __future__ import annotations

from pydantic import BaseModel

from app.config import KnowledgeParams
from app.knowledge.words import skill_level
from app.schemas.common import ExamId
from app.schemas.knowledge import (
    KnowledgeStateOut,
    Prerequisite,
    RootCauseOut,
    SkillWeight,
)

_PRIOR_P_RECALL = 0.5
_DEFAULT_EXAM: ExamId = "SAT_MATH"
# Разрыв, с которым в очередь попадает навык, который мы ни разу не проверяли,
# когда цель уже ниже приора. Размер — одна проверочная задача, как у навыка с
# недавним заблуждением в 20-B1-phase2.md §3.1 (`need` не ниже `weight · 0.05`).
_UNSEEN_GAP = 0.05


class QueueItem(BaseModel):
    """One skill in the study queue — memory-architecture §10.1."""

    skill_id: str
    exam_id: ExamId
    need: float
    urgency: float
    gap: float
    is_root: bool = False
    is_check: bool = False


def build_queue(
    states: list[KnowledgeStateOut],
    skill_weights: list[SkillWeight],
    prerequisites: list[Prerequisite],
    days_to_test: int,
    p_target: float,
    root_causes: list[RootCauseOut],
    params: KnowledgeParams,
) -> list[QueueItem]:
    """Rank skills by need, prerequisites first.

    need = weight · gap · urgency · root_boost
    gap = max(0, p_target − p_recall)
    urgency = 1 + max(0, (60 − days_to_test) / 60)
    root_boost = params.root_boost for skills that are roots of recent errors
    """
    state_by_skill: dict[str, KnowledgeStateOut] = {}
    for s in states:
        state_by_skill.setdefault(s.skill_id, s)

    boost_skills = {r.root_skill_id for r in root_causes}

    urgency = 1.0 + max(0.0, (60 - days_to_test) / 60)

    items: list[QueueItem] = []
    seen_math: set[str] = set()

    for sw in skill_weights:
        sid = sw.skill.id

        # общий math.* — один раз
        if sid.startswith("math."):
            if sid in seen_math:
                continue
            seen_math.add(sid)

        state = state_by_skill.get(sid)

        # closed — не в очереди
        if state is not None and skill_level(state, p_target, params) == "closed":
            continue

        p_recall = state.p_recall if state is not None else _PRIOR_P_RECALL
        confidence = state.confidence if state is not None else 0.0

        is_check = confidence < params.c_vis
        gap = max(0.0, p_target - p_recall)
        if gap <= 0:
            # Цель уже взята — но только если p_recall действительно что-то
            # значит. У непроверенного навыка это приор (0.5), а не знание, и
            # программа с низким порогом (ЕНТ 12 из 50 → p_target 0.24) иначе
            # вычёркивает из плана всё, чего мы ни разу не видели: ученик
            # получает пустую «Подготовку» вместо проверки. §3.1 убирает из
            # очереди только `level == closed`, и это правило уже выше.
            if not is_check:
                continue
            gap = _UNSEEN_GAP

        is_root = sid in boost_skills
        root_boost = params.root_boost if is_root else 1.0
        need = sw.weight * gap * urgency * root_boost

        exam_id: ExamId
        if state is not None:
            exam_id = state.exam_id
        elif sw.skill.exam_ids:
            exam_id = sw.skill.exam_ids[0]
        else:
            exam_id = _DEFAULT_EXAM

        items.append(
            QueueItem(
                skill_id=sid,
                exam_id=exam_id,
                need=need,
                urgency=urgency,
                gap=gap,
                is_root=is_root,
                is_check=is_check,
            )
        )

    items.sort(key=lambda x: -x.need)
    return items
