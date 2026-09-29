"""Diagnostic — adaptive descent by prerequisites — memory-architecture §8.4.

Pure state machine, no I/O.
Source: 20-B1-phase2.md §2.4.
"""

from __future__ import annotations

from app.config import KnowledgeParams
from app.schemas.diagnostic import DiagnosticResult, DiagnosticState
from app.schemas.knowledge import (
    AreaOut,
    KnowledgeStateOut,
    Prerequisite,
    RootCauseOut,
    SkillWeight,
)
from app.schemas.tasks import Grade


def start(
    exam_skills: list[SkillWeight],
    areas: list[AreaOut],
    prerequisites: list[Prerequisite],
    known_roots: list[RootCauseOut],
    budget: int | None,
    params: KnowledgeParams,
) -> DiagnosticState:
    """Build the initial state: budget by area share, roots first in pending_descent."""
    total_budget = budget if budget is not None else params.diag_base

    # Порядок навыков: по областям в порядке score_share, внутри — по weight
    share_by_area = {a.id: a.score_share for a in areas}
    ordered = sorted(
        exam_skills,
        key=lambda sw: (
            -share_by_area.get(sw.area_id, 0.0),
            -sw.weight,
            sw.skill.id,
        ),
    )
    budget_order = [sw.skill.id for sw in ordered]

    # Корни — первыми в pending_descent
    pending_descent: list[str] = []
    for root in known_roots:
        if root.root_skill_id not in pending_descent:
            pending_descent.append(root.root_skill_id)

    exam_id = exam_skills[0].skill.exam_ids[0] if exam_skills else "SAT_MATH"

    return DiagnosticState(
        exam_id=exam_id,  # type: ignore[arg-type]
        budget_left=total_budget,
        reserve_left=params.diag_reserve,
        asked=[],
        answered=0,
        pending_descent=pending_descent,
        reask_queue=[],
        roots_found=list(known_roots),
        trap_hits=[],
        firm=[],
        shaky=[],
        last_grade_correct=None,
        budget_order=budget_order,
        indirect=[],
    )


def next_skill(
    state: DiagnosticState,
    prerequisites: list[Prerequisite],
    params: KnowledgeParams,
) -> str | None:
    """Return the next skill to ask, or None if the run is done.

    Priority: reask_queue with count == 0 → pending_descent → next by budget order.
    """
    # 1. reask_queue: пара с нулевым счётчиком
    for skill_id, count in state.reask_queue:
        if count == 0:
            return skill_id

    # 2. pending_descent
    if state.pending_descent:
        return state.pending_descent[0]

    # 3. бюджет исчерпан?
    if state.budget_left <= 0:
        return None

    # 4. Следующий по порядку, кого ещё не спрашивали
    for skill_id in state.budget_order:
        if skill_id not in state.firm and skill_id not in state.shaky:
            return skill_id

    return None


def apply_answer(
    state: DiagnosticState,
    skill_id: str,
    grade: Grade,
    prerequisites: list[Prerequisite],
    params: KnowledgeParams,
) -> DiagnosticState:
    """Advance the state after one answer.

    - correct → firm; prerequisites get indirect evidence
    - incorrect → shaky; if reserve_left > 0, push the strongest prerequisite
      into pending_descent
    - trap hit → reask_queue.append((skill, diag_reask_after)); tick others
    """
    new_firm = list(state.firm)
    new_shaky = list(state.shaky)
    new_pending = [s for s in state.pending_descent if s != skill_id]
    new_reask = [(sid, c) for sid, c in state.reask_queue if sid != skill_id]
    new_reask = [(sid, max(0, c - 1)) for sid, c in new_reask]
    new_indirect = list(state.indirect)
    new_traps = list(state.trap_hits)
    new_roots = list(state.roots_found)

    on_descent = skill_id in state.pending_descent

    if grade.correct:
        if skill_id not in new_firm:
            new_firm.append(skill_id)
        for prereq in prerequisites:
            new_indirect.append((prereq.skill_id, params.prior_indirect_weight))
    else:
        if skill_id not in new_shaky:
            new_shaky.append(skill_id)
        # Спуск по предпосылкам, если есть резерв (смотрим на резерв ДО ответа)
        if state.reserve_left > 0 and prerequisites:
            strongest = max(prerequisites, key=lambda p: p.strength)
            if strongest.skill_id not in new_pending:
                new_pending.insert(0, strongest.skill_id)

    # Попадание в ловушку — отложить повторный вопрос
    if grade.matched_misconception_id is not None:
        new_traps.append(grade.matched_misconception_id)
        new_reask.append((skill_id, params.diag_reask_after))

    return state.model_copy(
        update={
            "budget_left": state.budget_left - 1,
            "reserve_left": max(0, state.reserve_left - (1 if on_descent else 0)),
            "answered": state.answered + 1,
            # `asked` — настоящие task_instance.id, чистый слой их не знает:
            # список ведёт apply.diagnostic._issue_next при выдаче задачи.
            # Здесь стояла uuid4()-заглушка, и asked[-1] указывал в никуда.
            "firm": new_firm,
            "shaky": new_shaky,
            "pending_descent": new_pending,
            "reask_queue": new_reask,
            "trap_hits": new_traps,
            "roots_found": new_roots,
            "last_grade_correct": grade.correct,
            "indirect": new_indirect,
        }
    )


def finish(
    state: DiagnosticState,
    states: dict[str, KnowledgeStateOut],
    params: KnowledgeParams,
) -> DiagnosticResult:
    """Final result: firm / shaky / roots / suspected / start_from + words."""
    # Навык, спрошенный повторно (ловушка), успевает побывать и там и там.
    # Последнее слово за shaky: это более осторожный и более поздний вывод.
    shaky = _unique(state.shaky)
    firm = [skill_id for skill_id in _unique(state.firm) if skill_id not in shaky]

    # start_from — самые нижние shaky, у которых нет shaky-предпосылок.
    # Без карты зависимостей считаем, что shaky — это стартовые точки.
    start_from = list(shaky)

    words = _words(state)

    return DiagnosticResult(
        firm=firm,
        shaky=shaky,
        roots=list(state.roots_found),
        # trap_hits — журнал попаданий, в итоге же нужен список самих ловушек
        suspected=_unique(state.trap_hits),
        start_from=start_from,
        words=words,
    )


# --- helpers ---


def _unique(values: list[str]) -> list[str]:
    """Без повторов, порядок первого появления."""
    return list(dict.fromkeys(values))


def _words(state: DiagnosticState) -> str:
    if not state.firm and not state.shaky:
        return "Замер не состоялся — нет данных."
    parts: list[str] = []
    if state.firm:
        parts.append(f"твёрдо: {len(state.firm)} навыков")
    if state.shaky:
        parts.append(f"шатко: {len(state.shaky)} навыков")
    if state.roots_found:
        parts.append(f"корней: {len(state.roots_found)}")
    if state.trap_hits:
        parts.append(f"подозрений на ловушки: {len(state.trap_hits)}")
    return ", ".join(parts) + "."
