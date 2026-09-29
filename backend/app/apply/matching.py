"""Matching and comparison for the route and the assistant's tools.

Ownership exception (docs/tz/phase3-agents.md §5.2 F13, §9 item 14): this is a
B3 file inside the B1 `apply/` package. `GET /matching` and the selection
tool `run_matching` must return the same numbers and the same order (жюри,
step 4) — so both call this one implementation instead of each assembling
the inputs itself. The rules stay in B1's pure `matching/*`.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from neo4j.exceptions import ServiceUnavailable, SessionExpired
from sqlalchemy.ext.asyncio import AsyncSession

from app import keys
from app.apply import soft
from app.db.repo import forecast as forecast_repo
from app.db.repo import profiles as profile_repo
from app.db.repo import programs as program_repo
from app.db.repo import texts as texts_repo
from app.errors import NotFound, ValidationFailed
from app.events.dispatch import RuleDeps
from app.graph.queries import kb
from app.knowledge.text_inputs import inputs_hash
from app.matching import compare as compare_rules
from app.matching import hard, rank, realism
from app.prompt_versions import text_versions
from app.schemas.common import ExamId, GeneratedTextStatus
from app.schemas.knowledge import ForecastOut, TestDate
from app.schemas.matching import CompareOut, FactorOut, MatchingOut, MatchOut
from app.schemas.profile import Profile
from app.schemas.programs import Program


async def _inputs(
    session: AsyncSession, deps: RuleDeps, student_id: UUID
) -> tuple[
    Profile, list[Program], dict[ExamId, ForecastOut], dict[ExamId, list[TestDate]]
]:
    profile = await profile_repo.get_profile(session, student_id)
    programs = await program_repo.list_all(session)
    saved = await program_repo.list_saved_programs(session, student_id)
    exam_ids: set[ExamId] = {
        requirement.exam_id
        for program in saved
        for requirement in program.requirements
        if requirement.exam_id is not None
    }
    forecasts = {
        exam_id: forecast
        for exam_id in exam_ids
        if (forecast := await forecast_repo.get(session, student_id, exam_id))
        is not None
    }
    all_exam_ids: set[ExamId] = {
        requirement.exam_id
        for program in programs
        for requirement in program.requirements
        if requirement.exam_id is not None
    }
    # Граф недоступен — подборка без фактора сроков (тест-дат нет).
    test_dates: dict[ExamId, list[TestDate]] = {}
    if deps.graph is not None:
        try:
            for exam_id in all_exam_ids:
                test_dates[exam_id] = await kb.list_test_dates(deps.graph, exam_id)
        except (ServiceUnavailable, SessionExpired):
            # Driver exists, graph is down: same answer as no graph.
            test_dates = {}
    return profile, programs, forecasts, test_dates


async def run_matching(
    session: AsyncSession, deps: RuleDeps, student_id: UUID, limit: int
) -> MatchingOut:
    """Hard filter → realism → rank; at least `min_candidates` items.

    Phase 4 adds two read-only layers on top: the soft-fit rows written by
    `jobs.soft_match` and the «почему реалистично» texts. Neither is
    generated here — a request never calls the model (§1.1). What is missing
    is queued through the outbox and the route answers with what it has.
    """
    profile, programs, forecasts, test_dates = await _inputs(session, deps, student_id)
    hard_results = hard.hard_filter(
        profile, programs, forecasts, test_dates, deps.now().date(), deps.params
    )
    levels = {
        item.program_id: realism.realism(item, deps.params) for item in hard_results
    }
    summary = (profile.traits.summary or "").strip()
    soft_rows, summary_digest = await soft.read_scores(
        session,
        student_id,
        summary,
        [item.program_id for item in hard_results],
        _soft_version(),
    )
    soft_scores: dict[str, float | None] = (
        {
            item.program_id: (
                soft_rows[item.program_id].score
                if item.program_id in soft_rows
                else None
            )
            for item in hard_results
        }
        if summary
        else {}
    )
    ranked = rank.rank(profile, hard_results, soft_scores, params=deps.params)
    by_program = {program.id: program for program in programs}
    by_hard = {item.program_id: item for item in hard_results}

    weight = float(deps.params.matching_priority_weights.get("program", 2))
    items: list[MatchOut] = []
    for item in ranked:
        row = soft_rows.get(item.program_id)
        factors = list(by_hard[item.program_id].factors)
        if summary:
            factors.append(_soft_factor(row, weight))
        items.append(
            MatchOut(
                program=by_program[item.program_id],
                realism=levels[item.program_id],
                factors=factors,
                assumptions=by_hard[item.program_id].assumptions,
                score=item.score,
                fits_text=row.fit_text if row is not None else None,
                soft_pending=bool(summary) and row is None,
                soft=row,
            )
        )

    shown = items[: max(limit, deps.params.min_candidates)]
    await _attach_realism_texts(session, deps, student_id, shown)
    if summary and any(item.soft_pending for item in shown):
        await _enqueue_soft(deps, student_id, summary_digest)

    return MatchingOut(
        items=shown,
        total=len(items),
        profile_readiness=profile.readiness,
        forecast_used=bool(forecasts),
        empty_reason=hard.explain_empty(hard_results) if not hard_results else None,
    )


def _soft_version() -> str:
    from app.prompt_versions import version_of

    return version_of("soft_match")


def _soft_factor(row, weight: float) -> FactorOut:
    return FactorOut(
        id="soft:environment",
        kind="soft",
        status="in_range" if row is not None and row.fit_text else "unknown",
        text=(
            row.fit_text
            if row is not None and row.fit_text
            else "подбираем под твои предпочтения"
        ),
        source=None,
        weight=weight,
    )


async def _enqueue_soft(deps: RuleDeps, student_id: UUID, digest: str) -> None:
    """Belt and braces: the `profile.updated` handler normally does this, so
    the route only steps in when that event was lost (§5.6)."""
    try:
        pending = await deps.redis.get(keys.soft_pending(str(student_id)))
    except Exception:  # noqa: BLE001 — Redis down: enqueue, dedup is ARQ's job
        pending = None
    current = pending.decode() if isinstance(pending, bytes) else pending
    if current == digest:
        return
    deps.jobs.enqueue(
        "bulk",
        "soft_match",
        job_id=f"softmatch:{student_id}:{digest[:12]}",
        student_id=str(student_id),
        program_ids=[],
    )


async def _attach_realism_texts(
    session: AsyncSession, deps: RuleDeps, student_id: UUID, items: list[MatchOut]
) -> None:
    """Fill `realism_text` from the cache; queue one job for what is missing."""
    versions, model = text_versions()
    version = versions["realism"]
    missing: list[str] = []
    for index, item in enumerate(items[: deps.params.realism_texts_limit]):
        digest = realism_hash(student_id, item, version, model)
        current, last = await texts_repo.get_current(
            session, student_id, "realism", item.program.id, digest
        )
        text, status = _read_text(current, last)
        items[index] = item.model_copy(
            update={"realism_text": text, "realism_text_status": status}
        )
        if status in ("generating", "stale", "failed") and current is None:
            missing.append(item.program.id)
    if not missing:
        return
    profile_digest = hashlib.sha256(
        "|".join(
            realism_hash(student_id, item, version, model) for item in items
        ).encode("utf-8")
    ).hexdigest()[:12]
    deps.jobs.enqueue(
        "bulk",
        "realism_texts",
        job_id=f"realism:{student_id}:{profile_digest}",
        defer_by=10,
        student_id=str(student_id),
        program_ids=missing,
    )


def realism_hash(
    student_id: UUID, item: MatchOut, prompt_version: str, model: str
) -> str:
    """The profile enters through the *factors*, not raw (§7.2): editing a
    field that does not move this program's factors keeps the cache warm."""
    return inputs_hash(
        "realism",
        student_id,
        item.program.id,
        {
            "realism": item.realism,
            "factors": [
                {
                    "id": factor.id,
                    "status": factor.status,
                    "text": factor.text,
                    "source": factor.source.label if factor.source else None,
                }
                for factor in item.factors
                if factor.kind == "hard"
            ],
            "assumptions": item.assumptions,
        },
        prompt_version,
        model,
    )


def _read_text(current, last) -> tuple[str | None, GeneratedTextStatus]:
    """The §3.5 status table, shared by the texts route and matching."""
    if current is not None and current.status == "ready" and current.text:
        return current.text, "ready"
    if last is not None and last.text:
        return last.text, "stale"
    if current is not None and current.status == "failed":
        return None, "failed"
    return None, "generating"


async def compare_programs(
    session: AsyncSession, deps: RuleDeps, student_id: UUID, program_ids: list[str]
) -> CompareOut:
    """Side-by-side comparison of 2–4 distinct, unflagged programs."""
    if (
        not 2 <= len(program_ids) <= 4
        or len(set(program_ids)) != len(program_ids)
        or not all(program_ids)
    ):
        raise ValidationFailed("select 2 to 4 distinct programs")
    profile = await profile_repo.get_profile(session, student_id)
    programs = []
    for program_id in program_ids:
        program = await program_repo.get_program(session, program_id)
        if program is None or program.flagged:
            raise NotFound("program not found")
        programs.append(program)
    hard_results = hard.hard_filter(
        profile, programs, {}, {}, deps.now().date(), deps.params
    )
    result = compare_rules.compare(profile, programs, hard_results)
    versions, model = text_versions()
    subject = ",".join(sorted(program_ids))
    digest = compare_hash(
        student_id, program_ids, result.rows, profile, versions["compare"], model
    )
    current, last = await texts_repo.get_current(
        session, student_id, "compare", subject, digest
    )
    conclusion, status = _read_text(current, last)
    if status != "ready" and current is None:
        deps.jobs.enqueue(
            "bulk",
            "compare_text",
            job_id=f"compare:{student_id}:{digest[:12]}",
            student_id=str(student_id),
            program_ids=list(program_ids),
        )
    return CompareOut.model_validate(
        {
            **result.model_dump(),
            "program_ids": program_ids,
            "conclusion": conclusion,
            "conclusion_status": status,
        }
    )


def compare_hash(
    student_id: UUID,
    program_ids: list[str],
    rows,
    profile,
    prompt_version: str,
    model: str,
) -> str:
    """Only the rows that actually differ and matter shape the conclusion."""
    return inputs_hash(
        "compare",
        student_id,
        ",".join(sorted(program_ids)),
        {
            "rows": [
                {"param": row.param, "values": row.values}
                for row in rows
                if row.differs and row.relevant_to_student
            ],
            "priorities": (profile.questionnaire.priorities.ranking.value or [])[:3],
            "summary_hash": soft.summary_hash(profile.traits.summary or ""),
        },
        prompt_version,
        model,
    )


async def graph_reachable(deps: RuleDeps) -> bool:
    """Whether the graph answers now; a driver that exists may still be down."""
    if deps.graph is None:
        return False
    try:
        await deps.graph.verify_connectivity()
    except Exception:  # noqa: BLE001 — any failure means "not answering"
        return False
    return True
