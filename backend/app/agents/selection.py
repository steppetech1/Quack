"""Selection assistant — one chat turn and its eight tools.

Source: docs/tz/phase3-agents.md §3.2–§3.3; product-logic §3.2; phase-1
registry: docs/tz/30-B2.md §5.2.

The assistant never decides matching, cost thresholds or requirement status
itself (product-logic §3.3: "not letting the language model into hard
factors") — it only talks, and reaches for one of these tools whenever a
turn needs a fact, a write, or a computation it isn't allowed to invent.
The stage of the dialogue and the tools on offer are decided by code, not by
the model; every number in the reply is checked against this turn's tool
results before `done`.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

import structlog
from neo4j.exceptions import ServiceUnavailable, SessionExpired
from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from app import keys
from app.agents import postcheck
from app.agents._deps import rule_deps
from app.agents.router import AgentDeps
from app.apply import matching as apply_matching
from app.config import settings
from app.db.repo import forecast as forecast_repo
from app.db.repo import profiles as profiles_repo
from app.db.repo import programs as programs_repo
from app.errors import NotFound, ValidationFailed
from app.events import store
from app.graph.queries import canonical as canonical_q
from app.graph.queries import kb as kb_q
from app.llm.loop import LoopEnd, run_tool_loop
from app.llm.prompts import load_prompt
from app.llm.tools import ToolCtx, ToolRegistry, tool
from app.matching import shift as shift_rules
from app.matching.directions import direction_matches
from app.roadmap.requirements import build_requirements
from app.schemas.agents import (
    AdmissionRouteResult,
    DatasetAggregateOut,
    DatasetProgram,
    ExamFormatResult,
    ExamThreshold,
    MatchCard,
    MatchingSnapshot,
    ProfileUpdateResult,
    ProgramFactsResult,
    RunMatchingResult,
    SaveProgramResult,
    SnapshotFactor,
    SnapshotItem,
    TuitionStats,
    TurnState,
)
from app.schemas.chat import (
    ChatCtx,
    ChatMessageIn,
    Done,
    MessageOut,
    StreamError,
    StreamEvent,
)
from app.schemas.common import ExamId, Source
from app.schemas.events import (
    EventIn,
    EventType,
    ProfileUpdatedPayload,
    ProgramSavedPayload,
)
from app.schemas.knowledge import ForecastOut
from app.schemas.llm import LLMMessage
from app.schemas.matching import CompareOut, MatchingOut
from app.schemas.profile import Profile, ProfileUpdateIn
from app.schemas.programs import Program

_logger = structlog.get_logger(__name__)

Stage = Literal["opening", "intake", "summary", "matching", "refine"]
STAGES: tuple[Stage, ...] = ("opening", "intake", "summary", "matching", "refine")

# Fields that move the matching first, then "about the person" (§3.2 intake).
MISSING_ORDER: tuple[str, ...] = (
    "direction.field",
    "preferences.budget_per_year",
    "preferences.grant_need",
    "preferences.countries",
    "level.grade",
    "academics.sat_score",
    "academics.ent_trial_score",
    "academics.ielts_score",
    "preferences.language",
    "pace.hours_per_week",
)
_MARK_WORDS = {"stated": "сказал", "assumed": "предположили", "default": "по умолчанию"}
_STAGE_RULES: dict[Stage, str] = {
    "opening": (
        "- одна фраза о том, что сделаю, и один открытый вопрос: что ищет и почему;\n"
        "- всё, что ученик уже сказал о себе, — сразу в профиль."
    ),
    "intake": (
        "- записать в профиль всё новое из сообщения;\n"
        "- не больше двух вопросов, сначала поля из строки missing (направление, "
        "бюджет или грант, страны, класс), потом «про человека»;\n"
        "- не спрашивать поля, у которых уже есть значение;\n"
        "- подборку запускать только если ученик прямо попросил показать."
    ),
    "summary": (
        "- резюме понимания из четырёх блоков: сильные стороны, ограничения, цель, "
        "черты;\n"
        "- отдельным списком — допущения;\n"
        "- попросить подтвердить или поправить;\n"
        "- run_matching не вызывать, если ученик не просил показать."
    ),
    "matching": (
        "- правки из ответа ученика — в профиль (update_profile);\n"
        "- затем run_matching;\n"
        "- карточки ученик видит сам: текст — короткая обвязка, уровни словами."
    ),
    "refine": (
        "- правка профиля → назвать, что сдвинулось в подборке;\n"
        "- сравнение (compare), сохранение (save_program) — по просьбе;\n"
        "- вопросы по данным — через инструменты."
    ),
}
_ALWAYS = (
    "update_profile",
    "query_dataset",
    "get_exam_format",
    "get_admission_route",
    "get_program_facts",
)
_WANTS_MATCHING_RE = re.compile(r"покаж|показ|подбер|вариант|программ[ыу]|список")
_SNAPSHOT_TTL_S = 7 * 24 * 3600
_SNAPSHOT_LINES = 10
_SHIFT_LINES = 5
_MAX_STEPS = 6
_POSTCHECK_MESSAGE = (
    "Ответ отозван: в нём есть данные, которых нет в результатах инструментов"
)
_EXAMS: tuple[ExamId, ...] = ("SAT_MATH", "ENT_MATH")


# --- stage and tool policy (code, not the model) ---


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def _wants_matching(text: str) -> bool:
    return bool(_WANTS_MATCHING_RE.search(_normalize(text)))


def _last_stage(history: list[MessageOut]) -> Stage | None:
    for message in reversed(history):
        if message.role == "assistant":
            mode = message.markup.mode if message.markup else None
            return mode if mode in STAGES else None  # type: ignore[return-value]
    return None


def _stage(
    history: list[MessageOut],
    profile: Profile,
    snapshot: MatchingSnapshot | None,
    text: str,
    params: Any = None,
) -> Stage:
    """The dialogue stage, checked in the order of the §3.2 table."""
    params = params or settings.knowledge
    if not any(message.role == "assistant" for message in history):
        return "opening"
    threshold = params.assistant_readiness_threshold
    if profile.readiness < threshold and snapshot is None:
        return "intake"
    last = _last_stage(history)
    if profile.readiness >= threshold and snapshot is None and last != "summary":
        return "summary"
    if last == "summary" or _wants_matching(text):
        return "matching"
    return "refine"


def _tool_policy(
    stage: Stage, user_text: str, snapshot: MatchingSnapshot | None
) -> ToolRegistry:
    """The subset of `SELECTION_TOOLS` offered to the model this turn."""
    names = list(_ALWAYS)
    if stage in ("summary", "matching", "refine") or (
        stage == "intake" and _wants_matching(user_text)
    ):
        names.append("run_matching")
    if stage in ("matching", "refine"):
        names += ["compare", "save_program"]
    return SELECTION_TOOLS.subset(names)


# --- system prompt ---


def _field_value(profile: Profile, path: str) -> tuple[Any, str]:
    section, leaf = path.split(".")
    field = getattr(getattr(profile.questionnaire, section), leaf)
    return field.value, field.mark


def _format_value(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={item}" for key, item in value.items())
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _questionnaire_paths() -> list[str]:
    return sorted(profiles_repo.QUESTIONNAIRE_PATHS)


def missing_fields(profile: Profile) -> list[str]:
    missing = [path for path in MISSING_ORDER if _field_value(profile, path)[0] is None]
    return missing


def render_profile(profile: Profile) -> str:
    lines = [f'<profile readiness="{profile.readiness:.2f}">']
    for path in _questionnaire_paths():
        value, mark = _field_value(profile, path)
        if value is None or value == [] or value == {}:
            continue
        lines.append(f"{path}: {_format_value(value)} ({_MARK_WORDS[mark]})")
    if profile.traits.verbatim:
        lines.append("traits.verbatim:")
        lines.extend(f"- «{quote}»" for quote in profile.traits.verbatim)
    if profile.traits.summary:
        lines.append(f"traits.summary: {profile.traits.summary}")
    lines.append(f"missing: {', '.join(missing_fields(profile)) or 'нет'}")
    lines.append("</profile>")
    return "\n".join(lines)


def profile_values(profile: Profile) -> list[float | date]:
    """Numbers and dates of the profile snapshot shown to the model."""
    values: list[float | date] = []

    def add(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float):
            values.append(float(value))
        elif isinstance(value, date):
            values.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                add(item)
        elif isinstance(value, list):
            for item in value:
                add(item)

    for path in _questionnaire_paths():
        add(_field_value(profile, path)[0])
    sat = profile.questionnaire.academics.sat_score.value
    if sat is not None:
        if sat > 800:
            values.append(float(round(sat / 2 / 10) * 10))
            values.append(float(sat / 2))
        else:
            values.append(float(sat * 2))
    return values


def render_snapshot(snapshot: MatchingSnapshot | None) -> str:
    if snapshot is None or not snapshot.items:
        return "нет"
    lines = ["<last_matching>"]
    lines.extend(
        f"{item.program_id} · {item.university} · {item.direction} · {item.realism}"
        for item in snapshot.items[:_SNAPSHOT_LINES]
    )
    lines.append("</last_matching>")
    return "\n".join(lines)


def _forecast_line(forecast: ForecastOut) -> str:
    parts = [f"{forecast.exam_id}: покрытие {forecast.coverage:.2f}"]
    if forecast.predicted_scaled is not None:
        parts.append(f"прогноз {forecast.predicted_scaled:g}")
    if forecast.note:
        parts.append(forecast.note)
    return " — ".join(parts)


_REALISM_WORDS = {
    "possible": "реалистично",
    "try": "стоит попробовать",
    "impossible": "маловероятно",
}


def render_knowledge(forecasts: list[ForecastOut], shifts: list[Any]) -> str:
    lines = [_forecast_line(forecast) for forecast in forecasts]
    lines.extend(
        f"после последнего разговора: {s.program_id} стал «"
        f"{_REALISM_WORDS.get(s.to_realism, s.to_realism)}»"
        for s in shifts[:_SHIFT_LINES]
        if s.from_realism != s.to_realism
    )
    return "\n".join(lines) if lines else "нет данных"


def forecast_values(forecasts: list[ForecastOut]) -> list[float]:
    values: list[float] = []
    for forecast in forecasts:
        values.append(forecast.coverage)
        if forecast.predicted_scaled is not None:
            values.append(forecast.predicted_scaled)
    return values


# --- snapshot in Redis ---


async def read_snapshot(deps: AgentDeps, student_id: Any) -> MatchingSnapshot | None:
    if deps.redis is None:
        return None
    try:
        raw = await deps.redis.get(keys.matching_snapshot(str(student_id)))
    except Exception:  # noqa: BLE001 — Redis down: "no snapshot"
        _logger.warning("matching_snapshot_unavailable", student_id=str(student_id))
        return None
    if raw is None:
        return None
    try:
        return MatchingSnapshot.model_validate_json(raw)
    except ValueError:
        return None


async def write_snapshot(
    deps: AgentDeps, student_id: Any, out: MatchingOut, now: datetime
) -> None:
    snapshot = MatchingSnapshot(
        items=[
            SnapshotItem(
                program_id=item.program.id,
                university=item.program.university,
                direction=item.program.direction,
                realism=item.realism,
                score=item.score,
                factors=[
                    SnapshotFactor(id=f.id, status=f.status) for f in item.factors
                ],
            )
            for item in out.items
        ],
        as_of=now,
    )
    try:
        await deps.redis.set(
            keys.matching_snapshot(str(student_id)),
            snapshot.model_dump_json(),
            ex=_SNAPSHOT_TTL_S,
        )
    except Exception:  # noqa: BLE001
        _logger.warning("matching_snapshot_write_failed", student_id=str(student_id))


# --- the turn ---


async def run(
    ctx: ChatCtx,
    message: ChatMessageIn,
    history: list[MessageOut],
    profile: Profile,
    deps: AgentDeps,
) -> AsyncIterator[StreamEvent]:
    """Drive one turn of the selection chat (product-logic §3.2)."""
    params = settings.knowledge
    now = datetime.now(UTC)
    snapshot = await read_snapshot(deps, ctx.student_id)
    stage = _stage(history, profile, snapshot, message.text, params)
    registry = _tool_policy(stage, message.text, snapshot)

    forecasts, shifts = await _knowledge(deps, ctx, snapshot, params)
    system = load_prompt("selection").render(
        profile=render_profile(profile),
        stage=stage,
        stage_rules=_STAGE_RULES[stage],
        snapshot=render_snapshot(snapshot),
        knowledge=render_knowledge(forecasts, shifts),
        today=now.date().isoformat(),
    )
    messages = [
        LLMMessage(role="system", content=system),
        *(LLMMessage(role=m.role, content=m.text) for m in history),
        LLMMessage(role="user", content=message.text),
    ]
    _logger.info(
        "chat_turn_started", kind="selection", chat_id=str(ctx.chat_id), stage=stage
    )

    tool_ctx = ToolCtx(
        student_id=str(ctx.student_id),
        deps=deps,
        request_id=ctx.request_id,
        chat=ctx,
        turn=TurnState(),
    )
    end: LoopEnd | None = None
    async for event in run_tool_loop(
        deps.llm, registry, messages, "chat", tool_ctx, max_steps=_MAX_STEPS
    ):
        if isinstance(event, LoopEnd):
            end = event
            continue
        yield event
        if isinstance(event, StreamError):
            _logger.info("chat_turn_done", kind="selection", error=event.code)
            return
    assert end is not None

    check = postcheck.check_facts(
        end.text_full,
        end.tool_results,
        scope="all",
        extra_values=[
            *profile_values(profile),
            *forecast_values(forecasts),
            *postcheck.numbers_and_dates(message.text),
        ],
        max_questions=params.assistant_max_questions,
    )
    _logger.info(
        "postcheck",
        kind="selection",
        scope="all",
        ok=check.ok,
        mismatches=check.mismatches,
        questions=check.questions,
        text_len=len(end.text_full),
        tools=[r.tool for r in end.tool_results],
    )
    if not check.ok:
        yield StreamError(code="postcheck_failed", message=_POSTCHECK_MESSAGE)
        return

    matched = any(
        r.tool == "run_matching" and r.error is None for r in end.tool_results
    )
    stage_after: Stage = "matching" if matched else stage
    _logger.info(
        "chat_turn_done",
        kind="selection",
        steps=end.steps,
        text_len=len(end.text_full),
        tools=[r.tool for r in end.tool_results],
    )
    yield Done(
        event_id=0,
        mode=stage_after,
        gave_task_instance_id=None,
        hint_level=None,
        referenced_skill_ids=[],
    )


async def _knowledge(
    deps: AgentDeps, ctx: ChatCtx, snapshot: MatchingSnapshot | None, params: Any
) -> tuple[list[ForecastOut], list[Any]]:
    """§10.3 aggregate: forecasts of the saved programs' exams, and what moved
    in the matching since the last conversation."""
    rd = rule_deps(deps)
    async with deps.pg() as session:
        saved = await programs_repo.list_saved_programs(session, ctx.student_id)
        exam_ids = sorted(
            {
                requirement.exam_id
                for program in saved
                for requirement in program.requirements
                if requirement.exam_id is not None
            }
        )
        forecasts = [
            forecast
            for exam_id in exam_ids
            if (forecast := await forecast_repo.get(session, ctx.student_id, exam_id))
            is not None
        ]
        shifts: list[Any] = []
        if snapshot is not None:
            current = await apply_matching.run_matching(
                session, rd, ctx.student_id, params.min_candidates * 2
            )
            shifts = shift_rules.diff(snapshot.items, current.items)
    return forecasts, shifts


# --- tools ---


class UpdateProfileArgs(BaseModel):
    path: str = Field(
        description=(
            "Dotted path into the student's profile, using the exact field "
            "names from product-logic §3.1's questionnaire (e.g. "
            "'preferences.countries', 'academics.sat_target', "
            "'preferences.grant_need') or 'traits.summary' / "
            "'traits.verbatim' for the free-text trait layer. A path "
            "outside these two trees is rejected."
        )
    )
    value: Any = Field(
        description=(
            "The new value, in that field's own shape (string, number, "
            "list, or an ISO 8601 date for date fields). For "
            "'traits.verbatim' this is the one quote to append, not the "
            "whole list; for 'traits.summary' it is the full replacement "
            "text of the running preference summary. "
            "For 'preferences.grant_need', allowed values are strictly: "
            "'only_grant' (только грант), 'preferred' (желательно), "
            "'not_needed' (не нужен). "
            "For 'academics.sat_score', write the math section score "
            "(up to 800, e.g. 650 for 1300 total)."
        )
    )
    by: Literal["assistant", "user"] = Field(
        default="assistant",
        description=(
            "Who is recorded as the source of this change. Leave at the "
            "default — the assistant tool call always attributes to "
            "itself, not to the student directly."
        ),
    )


def _coerce(profile: Profile, path: str, value: Any) -> tuple[Any, Any]:
    """(value in the field's own type, current value) — equality is checked
    on the coerced value, so "5000" and 5000 are the same answer."""
    try:
        if path == "traits.verbatim":
            new = TypeAdapter(str).validate_python(value)
            current = new if new in profile.traits.verbatim else None
            return new, current
        if path == "traits.summary":
            return TypeAdapter(str).validate_python(value), profile.traits.summary
        if path == "preferences.grant_need":
            if isinstance(value, bool):
                value = "preferred" if value else "not_needed"
            elif isinstance(value, str):
                v = value.lower().strip()
                if v in (
                    "true",
                    "yes",
                    "need",
                    "grant",
                    "желательно",
                    "нужен",
                    "хотелось бы",
                    "хочу",
                ):
                    value = "preferred"
                elif v in ("false", "no", "not_needed", "не нужен", "нет"):
                    value = "not_needed"
                elif v in ("only_grant", "только", "только грант"):
                    value = "only_grant"
        elif path == "level.grade" and isinstance(value, str):
            digits = re.findall(r"\d+", value)
            if digits:
                value = int(digits[0])
        elif path == "academics.sat_score" and isinstance(value, int | float | str):
            try:
                num = int(value)
                profiles_repo.check_number_bounds(path, num)
                if num > 800:
                    value = round(num / 2 / 10) * 10
                else:
                    value = num
            except (ValueError, TypeError):
                pass
        section, leaf = path.split(".")
        profiles_repo.check_number_bounds(path, value)
        field = getattr(getattr(profile.questionnaire, section), leaf)
        annotation = field.__class__.model_fields["value"].annotation
        return TypeAdapter(annotation).validate_python(value), field.value
    except ValidationError as error:
        detail = "; ".join(
            f"{e['loc'][0] if e.get('loc') else path}: {e.get('msg', '')}"
            for e in error.errors()
        )
        raise ValidationFailed(f"invalid profile value: {detail}") from error


@tool(
    name="update_profile",
    description=(
        "Writes one field of the student's profile — a questionnaire "
        "answer or the free-text trait summary/quote — so it persists and "
        "feeds matching, comparison and the prep side. Call it as soon as "
        "a message states a concrete fact about the student (a number, a "
        "country, a preference), not for facts that only matter within "
        "this reply. Match the fact to the field it belongs to: a sum of money "
        "is never a score. Money goes in preferences.budget_per_year and is per "
        "YEAR (a monthly figure is multiplied by 12). Living costs are not the "
        "tuition budget: keep them as traits.verbatim. Scores are exam points "
        "(ENT trial 0-140, SAT 400-1600, IELTS 0-9). If the message does not clearly "
        "answer the question you asked, write nothing and ask again."
    ),
    read_only=False,
)
async def update_profile(args: UpdateProfileArgs, ctx: ToolCtx) -> ProfileUpdateResult:
    if args.path not in profiles_repo.QUESTIONNAIRE_PATHS | {
        "traits.summary",
        "traits.verbatim",
    }:
        raise ValidationFailed("unknown profile path")
    student_id = _student(ctx)
    rd = rule_deps(ctx.deps)
    async with ctx.deps.pg() as session:
        profile = await profiles_repo.get_profile(session, student_id)
        value, current = _coerce(profile, args.path, args.value)
        if current is not None and current == value:
            return ProfileUpdateResult(
                path=args.path,
                value=_jsonable(value),
                mark=_mark(profile, args.path),
                readiness=profile.readiness,
                unchanged=True,
                shift=[],
                missing=missing_fields(profile),
            )
        # by всегда "assistant": инструмент записывает от своего имени (§3.3).
        updated = await profiles_repo.apply_profile_update(
            session,
            student_id,
            ProfileUpdateIn(path=args.path, value=value, by="assistant"),
        )
        await store.append(
            session,
            rd.redis,
            EventIn(
                type=EventType.profile_updated,
                payload=ProfileUpdatedPayload(
                    field=args.path, value=_jsonable(value), by="assistant"
                ).model_dump(mode="json"),
                student_id=student_id,
            ),
            rd,
            dispatch_event=True,
        )
        before = await read_snapshot(ctx.deps, student_id)
        shifts = []
        if before is not None:
            after = await apply_matching.run_matching(
                session,
                rd,
                student_id,
                max(len(before.items), rd.params.min_candidates),
            )
            shifts = shift_rules.diff(before.items, after.items)
        await session.commit()
    return ProfileUpdateResult(
        path=args.path,
        value=_jsonable(value),
        mark=_mark(updated, args.path),
        readiness=updated.readiness,
        unchanged=False,
        shift=shifts,
        missing=missing_fields(updated),
    )


def _mark(profile: Profile, path: str) -> str | None:
    if path.startswith("traits."):
        return None
    return _field_value(profile, path)[1]


def _jsonable(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    return value


def _student(ctx: ToolCtx) -> UUID:
    return UUID(str(ctx.student_id))


class RunMatchingArgs(BaseModel):
    limit: int = Field(
        default=5,
        description=(
            "How many ranked programs to return. Keep this small (the "
            "default is meant for normal use) — results are read out to "
            "the student one by one, not browsed as a list."
        ),
    )


def _source(program: Program) -> Source:
    return Source(
        label=program.university,
        url=program.source_url,
        checked_at=program.checked_at,
        is_demo=program.is_demo,
    )


@tool(
    name="run_matching",
    description=(
        "Runs the deterministic ranking algorithm (product-logic §3.3) "
        "over the program dataset using the student's current "
        "questionnaire, trait summary and, when available, a predicted "
        "exam score, and returns the top programs with their realism "
        "level and the hard/soft factors behind it. Call this to produce "
        "or refresh the actual recommendation list — never estimate or "
        "guess a ranking yourself."
    ),
    read_only=True,
)
async def run_matching(args: RunMatchingArgs, ctx: ToolCtx) -> RunMatchingResult:
    student_id = _student(ctx)
    rd = rule_deps(ctx.deps)
    limit = max(rd.params.min_candidates, min(10, args.limit))
    async with ctx.deps.pg() as session:
        out = await apply_matching.run_matching(session, rd, student_id, limit)
    items = out.items[:limit]
    await write_snapshot(
        ctx.deps, student_id, out.model_copy(update={"items": items}), rd.now()
    )
    ctx.turn["matching_calls"] = ctx.turn.get("matching_calls", 0) + 1
    return RunMatchingResult(
        count=len(items),
        total=out.total,
        forecast_used=out.forecast_used,
        empty_reason=out.empty_reason,
        profile_readiness=out.profile_readiness,
        items=[
            MatchCard(
                program_id=item.program.id,
                university=item.program.university,
                direction=item.program.direction,
                country=item.program.country,
                city=item.program.city,
                language=item.program.language,
                realism=item.realism,
                score=item.score,
                tuition_per_year=item.program.tuition_per_year,
                living_per_year=item.program.living_per_year,
                currency=item.program.currency,
                factors=item.factors,
                assumptions=item.assumptions,
                source=_source(item.program),
            )
            for item in items
        ],
    )


class CompareArgs(BaseModel):
    program_ids: list[str] = Field(
        description=(
            "Two to four program ids to place side by side — ids the "
            "student named or that a prior run_matching/get_program_facts "
            "call already surfaced, e.g. ['eth-cs', 'nu-cs']."
        )
    )


@tool(
    name="compare",
    description=(
        "Builds a side-by-side comparison of two to four specific "
        "programs (product-logic §3.4): where their requirement status "
        "differs, how they stack up on the student's own top priorities, "
        "and standard quality metrics (mobility, research, ranking, cost, "
        "duration, language, scholarships). Call it only once specific "
        "programs are on the table — it is not a way to discover new ones "
        "(use run_matching or query_dataset for that)."
    ),
    read_only=True,
)
async def compare(args: CompareArgs, ctx: ToolCtx) -> CompareOut:
    rd = rule_deps(ctx.deps)
    async with ctx.deps.pg() as session:
        return await apply_matching.compare_programs(
            session, rd, _student(ctx), args.program_ids
        )


class SaveProgramArgs(BaseModel):
    program_id: str = Field(
        description="Id of the single program to add to the student's saved list."
    )


@tool(
    name="save_program",
    description=(
        "Adds one program to the student's saved list (product-logic §3.5), "
        "which is what unlocks a preparation plan for its exams and its own "
        "deadlines and requirements view. Call it only right after the "
        "student has explicitly confirmed they want to save that specific "
        "program — never as a side effect of matching or comparing, and "
        "never to save more than one program at a time."
    ),
    read_only=False,
)
async def save_program(args: SaveProgramArgs, ctx: ToolCtx) -> SaveProgramResult:
    student_id = _student(ctx)
    rd = rule_deps(ctx.deps)
    async with ctx.deps.pg() as session:
        await programs_repo.save_program(session, student_id, args.program_id)
        await store.append(
            session,
            rd.redis,
            EventIn(
                type=EventType.program_saved,
                payload=ProgramSavedPayload(program_id=args.program_id).model_dump(
                    mode="json"
                ),
                student_id=student_id,
            ),
            rd,
            dispatch_event=True,
        )
        await session.commit()
        saved = await programs_repo.list_saved_programs(session, student_id)
        profile = await profiles_repo.get_profile(session, student_id)
        forecasts = {
            exam_id: await forecast_repo.get(session, student_id, exam_id)
            for exam_id in _EXAMS
        }
    exam_formats: dict = {}
    test_dates: dict = {}
    if rd.graph is not None:
        try:
            for exam_id in _EXAMS:
                exam_format = await canonical_q.get_exam_format(rd.graph, exam_id)
                if exam_format is not None:
                    exam_formats[exam_id] = exam_format
                test_dates[exam_id] = await kb_q.list_test_dates(rd.graph, exam_id)
        except (ServiceUnavailable, SessionExpired):
            exam_formats, test_dates = {}, {}
    exams = build_requirements(
        saved, profile, exam_formats, test_dates, forecasts, rd.params
    )
    return SaveProgramResult(
        program_id=args.program_id, saved_count=len(saved), exams=exams
    )


class ProgramFactsArgs(BaseModel):
    program_id: str = Field(description="Id of the program to fetch stored facts for.")


@tool(
    name="get_program_facts",
    description=(
        "Returns everything stored about one program: university, country, "
        "city, direction, language, duration, tuition and living cost, "
        "admission requirements with their thresholds, deadlines, "
        "scholarship notes and the free-text environment description, each "
        "with its source and check date. This is the only way to state a "
        "program's numbers, dates or requirements — anything stated "
        "without it fails the assistant's postcheck against tool results."
    ),
    read_only=True,
)
async def get_program_facts(args: ProgramFactsArgs, ctx: ToolCtx) -> ProgramFactsResult:
    async with ctx.deps.pg() as session:
        program = await programs_repo.get_program(session, args.program_id)
    if program is None or program.flagged:
        raise NotFound("program not found")
    return ProgramFactsResult(program=program, source=_source(program))


class AdmissionRouteArgs(BaseModel):
    country_id: str = Field(
        description=(
            "ISO alpha-2 country code (00-contracts.md §4.1), e.g. 'KZ' or 'US'."
        )
    )


class KnowledgeBaseUnavailable(Exception):
    """The admissions knowledge base (Neo4j) is down (product-logic §6.3)."""

    def __init__(self) -> None:
        super().__init__("knowledge base unavailable")


@tool(
    name="get_admission_route",
    description=(
        "Looks up how admission works in a country in general — "
        "application rounds, what programs there typically require, "
        "country-specific mechanics such as grant thresholds — from the "
        "shared admissions knowledge base (product-logic §5.0), "
        "independent of any single program. Use it for 'how does "
        "admission work in X' questions; use get_program_facts instead "
        "for one program's own requirements."
    ),
    read_only=True,
)
async def get_admission_route(
    args: AdmissionRouteArgs, ctx: ToolCtx
) -> AdmissionRouteResult:
    graph = ctx.deps.graph
    if graph is None:
        raise KnowledgeBaseUnavailable()
    country_id = args.country_id.upper()
    try:
        routes = await kb_q.get_admission_route(graph, country_id)
        facts = await kb_q.list_facts_about(graph, country_id)
    except (ServiceUnavailable, SessionExpired) as exc:
        raise KnowledgeBaseUnavailable() from exc
    return AdmissionRouteResult(country_id=country_id, routes=routes, facts=facts)


class ExamFormatArgs(BaseModel):
    exam_id: ExamId = Field(description="Which exam's format to look up.")


@tool(
    name="get_exam_format",
    description=(
        "Returns the official structure of one exam from the knowledge "
        "base: sections, number and type of items, timing, scoring and "
        "partial-credit rules, calculator policy, adaptivity, and the "
        "area/difficulty distribution used to build mocks. Call it "
        "whenever a reply needs a specific exam fact (section count, "
        "points, whether a calculator is allowed) instead of relying on "
        "memory — exam facts stated without it fail postcheck."
    ),
    read_only=True,
)
async def get_exam_format(args: ExamFormatArgs, ctx: ToolCtx) -> ExamFormatResult:
    graph = ctx.deps.graph
    if graph is None:
        raise KnowledgeBaseUnavailable()
    try:
        exam_format = await canonical_q.get_exam_format(graph, args.exam_id)
        dates = await kb_q.list_test_dates(graph, args.exam_id)
        facts = await kb_q.list_facts_about(graph, args.exam_id)
    except (ServiceUnavailable, SessionExpired) as exc:
        raise KnowledgeBaseUnavailable() from exc
    if exam_format is None:
        raise NotFound("exam format not found")
    today = datetime.now(UTC).date()
    upcoming = sorted((d for d in dates if d.date > today), key=lambda d: d.date)
    return ExamFormatResult(format=exam_format, test_dates=upcoming[:3], facts=facts)


class QueryDatasetArgs(BaseModel):
    question: str = Field(
        description=(
            "The student's question about the program dataset, close to "
            "their own words, e.g. 'where can I study my major' or 'how "
            "much does it cost in Germany'."
        )
    )
    country: str | None = Field(
        default=None,
        description=(
            "ISO alpha-2 country code to narrow the search, if the "
            "question names one country. Leave unset otherwise."
        ),
    )
    direction: str | None = Field(
        default=None,
        description=(
            "Field of study to narrow the search, if the question names "
            "one. Leave unset otherwise."
        ),
    )


_DATASET_LIST = 15


def aggregate_dataset(
    programs: list[Program],
    question: str,
    country: str | None,
    direction: str | None,
    profile_countries: list[str],
) -> DatasetAggregateOut:
    """The deterministic aggregate behind `query_dataset` (pure)."""
    filters: dict[str, Any] = {"country": country, "direction": direction}
    countries = {country.upper()} if country else set()
    profile_filtered = False
    if not countries and profile_countries:
        countries = {c.upper() for c in profile_countries}
        profile_filtered = True
        filters["country"] = sorted(countries)
    selected = [
        p
        for p in programs
        if not p.flagged
        and (not countries or p.country.upper() in countries)
        and (direction is None or direction_matches(direction, p.direction))
    ]

    by_country: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    languages: dict[str, int] = {}
    thresholds: dict[str, list[float]] = {}
    for p in selected:
        by_country[p.country] = by_country.get(p.country, 0) + 1
        by_direction[p.direction] = by_direction.get(p.direction, 0) + 1
        languages[p.language] = languages.get(p.language, 0) + 1
        for requirement in p.requirements:
            if (
                requirement.type == "exam_score"
                and requirement.exam_id is not None
                and requirement.threshold is not None
            ):
                thresholds.setdefault(requirement.exam_id, []).append(
                    requirement.threshold
                )
    tuitions = [p.tuition_per_year for p in selected if p.tuition_per_year is not None]
    tuition = TuitionStats(
        min=min(tuitions) if tuitions else None,
        median=float(statistics.median(tuitions)) if tuitions else None,
        max=max(tuitions) if tuitions else None,
        currency_mix=sorted(
            {p.currency for p in selected if p.tuition_per_year is not None}
        ),
    )
    sources: list[Source] = []
    for p in selected:
        source = _source(p)
        if source not in sources:
            sources.append(source)
    return DatasetAggregateOut(
        question=question,
        filters=filters,
        count=len(selected),
        by_country=by_country,
        by_direction=by_direction,
        tuition=tuition,
        free_count=sum(1 for p in selected if p.tuition_per_year == 0),
        with_scholarship_note=sum(1 for p in selected if p.scholarships_note),
        languages=languages,
        exam_thresholds={
            exam_id: ExamThreshold(min=min(values), max=max(values))
            for exam_id, values in thresholds.items()
        },
        programs=[
            DatasetProgram(
                program_id=p.id,
                university=p.university,
                city=p.city,
                country=p.country,
                tuition_per_year=p.tuition_per_year,
                currency=p.currency,
            )
            for p in selected[:_DATASET_LIST]
        ],
        sources=sources[:_DATASET_LIST],
        profile_filtered=profile_filtered,
    )


@tool(
    name="query_dataset",
    description=(
        "Answers an aggregate or exploratory question over the whole "
        "program dataset — availability by field of study, typical cost "
        "in a country, how many options exist for a direction — without "
        "pinning to one program (product-logic §3.2's 'вопрос по "
        "данным'). Use it for 'what's out there' questions; use "
        "get_program_facts for a program the student has already named, "
        "and run_matching to actually produce the ranked recommendation "
        "list."
    ),
    read_only=True,
)
async def query_dataset(args: QueryDatasetArgs, ctx: ToolCtx) -> DatasetAggregateOut:
    student_id = _student(ctx)
    async with ctx.deps.pg() as session:
        programs = await programs_repo.list_all(session)
        profile = await profiles_repo.get_profile(session, student_id)
    _logger.info("query_dataset", question_len=len(args.question))
    return aggregate_dataset(
        programs,
        args.question,
        args.country,
        args.direction,
        profile.questionnaire.preferences.countries.value or [],
    )


SELECTION_TOOLS = ToolRegistry()
for _spec in (
    update_profile,
    run_matching,
    compare,
    save_program,
    get_program_facts,
    get_admission_route,
    get_exam_format,
    query_dataset,
):
    SELECTION_TOOLS.register(_spec)
del _spec
