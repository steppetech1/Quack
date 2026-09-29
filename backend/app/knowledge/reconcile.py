"""Reconcile a task answer — memory-architecture-quack.md §8.2, steps 2–8.

Pure function: takes the instance, the grade, the current state and returns
the new evidence, state, misconception change and root causes. No I/O.

Source: 20-B1-phase2.md §2.1.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from app.config import KnowledgeParams
from app.knowledge import hlr, weights
from app.knowledge.misconceptions import next_status
from app.knowledge.roots import rule_root
from app.knowledge.words import state_words
from app.schemas.events import TaskAnsweredPayload
from app.schemas.knowledge import (
    AreaOut,
    EvidenceContext,
    EvidenceIn,
    ExamId,
    KnowledgeStateOut,
    MisconceptionChange,
    MisconceptionStateOut,
    Prerequisite,
    ReconcileResult,
    SkillWeight,
)
from app.schemas.tasks import Grade, TaskInstance

if TYPE_CHECKING:
    from app.schemas.knowledge import RootCauseOut


_MOCK_MODES = {"mock_set", "mock_topic", "mock_misconception"}


def reconcile_task_answer(
    instance: TaskInstance,
    grade: Grade,
    payload: TaskAnsweredPayload,
    state: KnowledgeStateOut | None,
    prereq_states: list[tuple[Prerequisite, KnowledgeStateOut | None]],
    misc_states: list[MisconceptionStateOut],
    n_seen: int,
    exam_ids: list[ExamId],
    params: KnowledgeParams,
    now: datetime,
    event_id: int | None = None,
    p_target: float | None = None,
) -> ReconcileResult:
    """Apply one task answer (§8.2 steps 3–8).

    Steps 1 (event) and 9–10 (persistence, rebuild) are done by apply/*.
    """
    # --- direction / share ---
    if grade.correct:
        direction: int = 1
        share: float | None = None
    elif grade.partial is not None:
        direction = 0
        share = grade.partial
    else:
        direction = -1
        share = None

    # --- source / tier from mode ---
    # Ответ на задачу, выданную в чате репетитора (phase3 F7): source="chat",
    # kind="task_in_chat" — ярус 2, вес 0.8 (§4.3). Ответ записывает правило
    # наблюдателя, и свидетельство честно помечено как пришедшее из чата;
    # вес тот же, что у задачи темы, потому что это решение задачи с ключом.
    mode = payload.mode
    if mode in _MOCK_MODES:
        source = "mock"
    elif mode == "diagnostic":
        source = "diagnostic"
    elif mode == "chat":
        source = "chat"
    else:
        source = "task"

    kind = "task_in_chat" if mode == "chat" else "task"
    tier = weights.tier_for(source, mode, kind=kind)

    matched = grade.correct or grade.matched_misconception_id is not None

    weight = weights.evidence_weight(
        source,
        mode,
        None if kind == "task" else kind,
        seen_before=n_seen > 0,
        matched=matched,
        params=params,
    )

    df = hlr.difficulty_factor(instance.difficulty)

    ctx = EvidenceContext(
        task_type=instance.type,
        difficulty=instance.difficulty,
        tags=instance.tags or None,
        mode=payload.mode,
        time_ratio=payload.time_spent_sec / max(1, instance.time_reference_sec),
        session_minute=payload.session_minute,
        after_guideline=payload.after_guideline,
        hint_level_before=payload.hint_level_before,
        topic_skill_id=instance.skill_id,
        session_id=None,
        instance_id=instance.id,
        message_id=None,
    )

    ev_fields: dict = dict(
        event_id=event_id or 0,
        ordinal=0,
        skill_id=instance.skill_id,
        exam_id=instance.exam_id,
        kind=kind,
        tier=tier,
        source=source,
        weight=weight,
        direction=direction,
        share=share,
        difficulty_factor=df,
        summary=None,
        context=ctx,
        observed_at=now,
        extractor_version=None,
    )
    primary_ev = EvidenceIn(**ev_fields)
    evidence: list[EvidenceIn] = [primary_ev]

    if grade.matched_misconception_id is not None:
        misc_name = _misc_name(misc_states, grade.matched_misconception_id)
        # ordinal=1 — иначе MERGE в графе по (event_id, skill_id) схлопнул бы
        # попадание в заблуждение с основным свидетельством того же события.
        evidence.append(
            EvidenceIn(
                **{
                    **ev_fields,
                    "ordinal": 1,
                    "kind": "misconception_hit",
                    "summary": misc_name,
                }
            )
        )

    state_after = hlr.apply_evidence(state, primary_ev, params=params)

    cross_exam_state: KnowledgeStateOut | None = None
    if len(exam_ids) > 1 and instance.skill_id.startswith("math."):
        other_exam = next((e for e in exam_ids if e != instance.exam_id), None)
        if other_exam is not None:
            cross_ev = primary_ev.model_copy(
                update={
                    "exam_id": other_exam,
                    "weight": primary_ev.weight * params.transfer_cross_exam,
                }
            )
            cross = hlr.apply_evidence(None, cross_ev, params=params)
            cross_exam_state = cross.model_copy(
                update={"exam_id": other_exam, "has_strong": False}
            )

    misconception_change = _apply_misconception(
        instance, grade, tier, misc_states, params
    )

    root_causes: list[RootCauseOut] = []
    if direction == -1:
        root = rule_root(instance.skill_id, prereq_states, params)
        if root is not None:
            root_causes.append(root.model_copy(update={"created_at": now}))

    # Цель по экзамену приходит из roadmap.requirements через apply; без неё
    # (прямые вызовы, тесты) остаётся верхняя граница params.p_target_max.
    words = state_words(
        state_after, params.p_target_max if p_target is None else p_target, params
    )

    return ReconcileResult(
        evidence=evidence,
        state_after=state_after,
        cross_exam_state=cross_exam_state,
        misconception_change=misconception_change,
        root_causes=root_causes,
        words=words,
    )


def _misc_name(states: list[MisconceptionStateOut], misc_id: str) -> str:
    for m in states:
        if m.misconception_id == misc_id:
            return m.name
    return misc_id


def _tested_misconceptions(instance: TaskInstance) -> set[str]:
    ids: set[str] = set()
    for opt in instance.options:
        if opt.misconception_id:
            ids.add(opt.misconception_id)
    for t in instance.trap_answers:
        if t.misconception_id:
            ids.add(t.misconception_id)
    return ids


def _apply_misconception(
    instance: TaskInstance,
    grade: Grade,
    tier: int,
    misc_states: list[MisconceptionStateOut],
    params: KnowledgeParams,
) -> MisconceptionChange | None:
    strong = weights.is_strong(tier)  # type: ignore[arg-type]

    if grade.matched_misconception_id is not None:
        existing = next(
            (
                m
                for m in misc_states
                if m.misconception_id == grade.matched_misconception_id
            ),
            None,
        )
        if existing is None:
            return MisconceptionChange(
                misconception_id=grade.matched_misconception_id,
                from_status=None,
                to_status="suspected",
                counters={
                    "occurrence_count": 1,
                    "strong_count": int(strong),
                    "consecutive_avoided": 0,
                },
            )

        occ = existing.occurrence_count + 1
        strong_count = existing.strong_count + (1 if strong else 0)
        new_status = next_status(
            existing.status,
            event="hit",
            strong=strong,
            occurrence_count=occ,
            strong_count=strong_count,
            consecutive_avoided=0,
            strong_at_dispute=existing.strong_count,
            disputed_at=None,
            previous_status=existing.status,
            params=params,
        )
        return MisconceptionChange(
            misconception_id=existing.misconception_id,
            from_status=existing.status,
            to_status=new_status,
            counters={
                "occurrence_count": occ,
                "strong_count": strong_count,
                "consecutive_avoided": 0,
            },
        )

    if grade.correct:
        tested = _tested_misconceptions(instance)
        for m in misc_states:
            if m.status == "confirmed" and m.misconception_id in tested:
                avoided = m.consecutive_avoided + 1
                new_status = next_status(
                    "confirmed",
                    event="avoided",
                    strong=False,
                    occurrence_count=m.occurrence_count,
                    strong_count=m.strong_count,
                    consecutive_avoided=avoided,
                    strong_at_dispute=m.strong_count,
                    disputed_at=None,
                    previous_status="confirmed",
                    params=params,
                )
                return MisconceptionChange(
                    misconception_id=m.misconception_id,
                    from_status="confirmed",
                    to_status=new_status,
                    counters={
                        "occurrence_count": m.occurrence_count,
                        "strong_count": m.strong_count,
                        "consecutive_avoided": avoided,
                    },
                )

    return None


# --- chat observations (memory-architecture §8.1, phase3 §3.10) ---

_CROSS_EXAMS: tuple[ExamId, ...] = ("SAT_MATH", "ENT_MATH")


def reconcile_chat_evidence(
    evidence: EvidenceIn,
    state: KnowledgeStateOut | None,
    misc_states: list[MisconceptionStateOut],
    misconception_id: str | None,
    avoided: bool,
    params: KnowledgeParams,
    now: datetime,
    *,
    cross_state: KnowledgeStateOut | None = None,
    p_target: float | None = None,
) -> ReconcileResult:
    """Apply one observer evidence to the skill state and, optionally, to a
    misconception state. Pure.

    - the skill state goes through `hlr.apply_evidence`, which already caps a
      tier-3 observation at `p_chat_cap` and sets `has_strong` only for tier
      ≤ 2 — exactly as for tasks;
    - `misconception_id` with `avoided=False` is a hit (`solution_step`
      incorrect): `occurrence_count += 1`, `strong_count += 1` for a strong
      tier; `confirmed` is reachable only with `strong_count ≥ 1` (§5.1);
    - `avoided=True` is `avoided_trap`: `consecutive_avoided += 1`; without an
      existing state there is nothing to avoid and no change is returned (the
      rule skips such an observation before calling this);
    - a shared `math.*` skill also moves the other exam's state, weighted by
      `transfer_cross_exam` and never made strong by it (as for tasks).
    """
    state_after = hlr.apply_evidence(state, evidence, params=params)

    cross_exam_state: KnowledgeStateOut | None = None
    if evidence.skill_id.startswith("math."):
        other_exam = next((e for e in _CROSS_EXAMS if e != evidence.exam_id), None)
        if other_exam is not None:
            cross_ev = evidence.model_copy(
                update={
                    "exam_id": other_exam,
                    "weight": evidence.weight * params.transfer_cross_exam,
                }
            )
            cross = hlr.apply_evidence(cross_state, cross_ev, params=params)
            cross_exam_state = cross.model_copy(
                update={
                    "exam_id": other_exam,
                    "has_strong": cross_state.has_strong
                    if cross_state is not None
                    else False,
                }
            )

    change: MisconceptionChange | None = None
    if misconception_id is not None:
        change = _chat_misconception_change(
            misconception_id, evidence.tier, misc_states, avoided, params
        )

    words = state_words(
        state_after, params.p_target_max if p_target is None else p_target, params
    )
    return ReconcileResult(
        evidence=[evidence],
        state_after=state_after,
        cross_exam_state=cross_exam_state,
        misconception_change=change,
        root_causes=[],
        words=words,
    )


def _chat_misconception_change(
    misconception_id: str,
    tier: int,
    misc_states: list[MisconceptionStateOut],
    avoided: bool,
    params: KnowledgeParams,
) -> MisconceptionChange | None:
    strong = weights.is_strong(tier)  # type: ignore[arg-type]
    existing = next(
        (m for m in misc_states if m.misconception_id == misconception_id), None
    )
    if avoided:
        if existing is None:
            return None
        consecutive = existing.consecutive_avoided + 1
        new_status = next_status(
            existing.status,
            event="avoided",
            strong=False,
            occurrence_count=existing.occurrence_count,
            strong_count=existing.strong_count,
            consecutive_avoided=consecutive,
            strong_at_dispute=existing.strong_count,
            disputed_at=None,
            previous_status=existing.status,
            params=params,
        )
        return MisconceptionChange(
            misconception_id=misconception_id,
            from_status=existing.status,
            to_status=new_status,
            counters={
                "occurrence_count": existing.occurrence_count,
                "strong_count": existing.strong_count,
                "consecutive_avoided": consecutive,
            },
        )

    if existing is None:
        return MisconceptionChange(
            misconception_id=misconception_id,
            from_status=None,
            to_status="suspected",
            counters={
                "occurrence_count": 1,
                "strong_count": int(strong),
                "consecutive_avoided": 0,
            },
        )
    occurrence = existing.occurrence_count + 1
    strong_count = existing.strong_count + (1 if strong else 0)
    new_status = next_status(
        existing.status,
        event="hit",
        strong=strong,
        occurrence_count=occurrence,
        strong_count=strong_count,
        consecutive_avoided=0,
        strong_at_dispute=existing.strong_count,
        disputed_at=None,
        previous_status=existing.status,
        params=params,
    )
    return MisconceptionChange(
        misconception_id=misconception_id,
        from_status=existing.status,
        to_status=new_status,
        counters={
            "occurrence_count": occurrence,
            "strong_count": strong_count,
            "consecutive_avoided": 0,
        },
    )


# --- priors from profile (memory-architecture §4.8) ---


def reconcile_prior(
    field: str,
    value,
    exam_skills: list[SkillWeight],
    areas: list[AreaOut],
    states: dict[str, KnowledgeStateOut],
    params: KnowledgeParams,
    now: datetime,
) -> list[tuple[EvidenceIn, KnowledgeStateOut]]:
    """Convert one profile field into priors for skills without strong state."""
    if field == "academics.self_assessment":
        return _prior_from_self_assessment(
            value, exam_skills, areas, states, params, now
        )
    if field in ("academics.ent_trial_score", "academics.sat_score"):
        return _prior_from_trial_score(value, exam_skills, states, params, now)
    return []


def _prior_from_self_assessment(
    value,
    exam_skills: list[SkillWeight],
    areas: list[AreaOut],
    states: dict[str, KnowledgeStateOut],
    params: KnowledgeParams,
    now: datetime,
) -> list[tuple[EvidenceIn, KnowledgeStateOut]]:
    if not isinstance(value, dict):
        return []

    area_levels: dict[str, int] = {}
    for area in areas:
        for key, lvl in value.items():
            if key == area.id or key == area.name or key in area.id:
                try:
                    area_levels[area.id] = int(lvl)
                except (TypeError, ValueError):
                    continue
                break

    out: list[tuple[EvidenceIn, KnowledgeStateOut]] = []
    for sw in exam_skills:
        if sw.area_id not in area_levels:
            continue
        existing = states.get(sw.skill.id)
        if existing is not None and existing.confidence >= params.c_vis:
            continue

        level = area_levels[sw.area_id]
        p_at_obs = _p_from_self_level(level)
        exam_id = sw.skill.exam_ids[0] if sw.skill.exam_ids else "SAT_MATH"

        ev = EvidenceIn(
            event_id=0,
            skill_id=sw.skill.id,
            exam_id=exam_id,
            kind="self_report",
            tier=3,
            source="self_report",
            weight=0.1,
            direction=1,
            share=None,
            difficulty_factor=1.0,
            summary=f"самооценка: {level}/5",
            context=EvidenceContext(),
            observed_at=now,
            extractor_version=None,
        )
        state = KnowledgeStateOut(
            skill_id=sw.skill.id,
            exam_id=exam_id,
            p_recall=p_at_obs,
            p_at_obs=p_at_obs,
            half_life_h=params.h0_prior,
            confidence=0.15,
            evidence_mass=0.1,
            n_correct=0,
            n_incorrect=0,
            n_partial=0,
            has_strong=False,
            last_observed_at=now,
            created_at=now,
        )
        out.append((ev, state))
    return out


def _prior_from_trial_score(
    value,
    exam_skills: list[SkillWeight],
    states: dict[str, KnowledgeStateOut],
    params: KnowledgeParams,
    now: datetime,
) -> list[tuple[EvidenceIn, KnowledgeStateOut]]:
    if not isinstance(value, (int, float)):
        return []

    total_weight = sum(sw.weight for sw in exam_skills)
    if total_weight <= 0:
        return []
    if float(value) > total_weight:
        # Балл вне шкалы секции: «пробный ЕНТ 95» — это сумма по пяти
        # предметам, а не математика из 50. Раньше такое значение обрезалось
        # до 1.0, то есть анкета заявляла идеальное знание всех навыков и
        # выносила их из плана. Молчать честнее, чем выдумать приор.
        return []
    p_at_obs = max(0.0, min(1.0, float(value) / total_weight))

    out: list[tuple[EvidenceIn, KnowledgeStateOut]] = []
    for sw in exam_skills:
        existing = states.get(sw.skill.id)
        if existing is not None and existing.confidence >= params.c_vis:
            continue

        exam_id = sw.skill.exam_ids[0] if sw.skill.exam_ids else "SAT_MATH"
        ev = EvidenceIn(
            event_id=0,
            skill_id=sw.skill.id,
            exam_id=exam_id,
            kind="self_report",
            tier=3,
            source="self_report",
            weight=0.1,
            direction=1,
            share=None,
            difficulty_factor=1.0,
            summary=f"пробный балл: {value}",
            context=EvidenceContext(),
            observed_at=now,
            extractor_version=None,
        )
        state = KnowledgeStateOut(
            skill_id=sw.skill.id,
            exam_id=exam_id,
            p_recall=p_at_obs,
            p_at_obs=p_at_obs,
            half_life_h=params.h0_prior,
            confidence=0.15,
            evidence_mass=0.1,
            n_correct=0,
            n_incorrect=0,
            n_partial=0,
            has_strong=False,
            last_observed_at=now,
            created_at=now,
        )
        out.append((ev, state))
    return out


def _p_from_self_level(level: int) -> float:
    """1–5 → p_at_obs: ≥4 → 0.75, 3 → 0.5, ≤2 → 0.3."""
    if level >= 4:
        return 0.75
    if level == 3:
        return 0.5
    return 0.3
