"""The check of a new canvas line: the cascade of ТЗ Ф4 WP21.4 (Т-§15.4, D-C-158).

For every new line of a canvas opened by a task:

1. **Symbolic transition** from the previous line (`/v1/math/transition`, WP11.5) when both
   lines parse. A discrepancy is a confirmed error (`observation.diagnosed`, `method =
   symbolic`, `kind = error`); a confirmed transition gives nothing — a step is no evidence of
   a level (Т-§15.4 п.1).
2. **Quick diagnosis** `canvas_diagnoser@1` (Spark, P0) when the symbolic check is impossible,
   and, for a confirmed discrepancy, to name its gap (`WP5.1`: "символьное расхождение с
   указанием заблуждения моделью"). The model also matches the line with the solution space:
   another valid way and "nothing matched" are not errors (D-C-158 п.2). A diagnosis of an error
   is `observation.diagnosed {method: model}`, `question` below `mark_min_confidence`
   (D-C-136); no error — no event (D-C-120).
3. **Second opinion** for a `question` (`second_opinion@1`, Т-§7.7): `observation.
   second_opinion {target_seq, agreed}`; agreed — the mark becomes `error`, not agreed — it is
   removed quietly. Until it answers, the mark is only a question (Т-§15.4 п.4).

What the board shows (WP21.5, Q-A-3 п.1): `error` only for a symbolic discrepancy or an agreed
second opinion, `question` for an unsure model diagnosis, nothing for a sure model diagnosis
without confirmation. The cascade only decides and writes the log; when and whether the learner
sees the mark is `quack.canvas.interventions`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from redis.asyncio import Redis

from quack.compute_client.client import ComputeClient, ComputeUnavailableError
from quack.compute_client.math_contracts import MathInput, NumericParams, TransitionRequest
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import (
    CanvasLineCommitted,
    HandwritingCorrected,
    ObservationDiagnosed,
    TaskCreated,
)
from quack.events.log import NewEvent, append_events
from quack.events.records import DiagnosisRecord
from quack.graph.read import GraphReader, MemorySlice, SliceFocus
from quack.llm.errors import LLMError
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.logging import get_logger
from quack.practice.repo import task_events
from quack.practice.role_outputs import ModelVerdictOutput, SolutionSpace
from quack.practice.task_store import TaskDocument, get_task

from .diagnosis import CanvasDiagnoserOutput, diagnosis_record

log = get_logger(__name__)

DIAGNOSER_ROLE = "canvas_diagnoser"
SECOND_OPINION_ROLE = "second_opinion"
NONE = "(none)"
# A memory read outside a turn: nothing is "in work" (as `quack.agents.delivery.NO_SESSION`).
_NO_SESSION = UUID(int=0)
# A confirmed discrepancy the model could not name is an error of execution of a known step.
SYMBOLIC_FALLBACK_GAP = "execution"

type Mark = Literal["error", "question"]

_LINE_EVENTS = "('canvas.line_committed', 'canvas.line_erased', 'handwriting.corrected')"
_LINES = (
    "SELECT seq, type, payload FROM core.events WHERE learner_id = %(learner_id)s "
    f"AND type IN {_LINE_EVENTS} AND payload ->> 'canvas_id' = %(canvas_id)s ORDER BY seq"
)


@dataclass(frozen=True)
class StepEnv:
    """What the check of one canvas needs; `reader` is `None` when memory is not at hand (the
    diagnoser then gets no known misconceptions, D-C-158 п.6)."""

    db: Db
    llm: LLMGateway
    compute: ComputeClient
    settings: Settings
    learner_id: UUID
    session_id: UUID | None
    canvas_id: UUID
    task_id: UUID | None
    reader: GraphReader | None = None
    redis: Redis | None = None


@dataclass(frozen=True)
class StepOutcome:
    """The result of the cascade for one line. `mark` is what the board may show (see the
    module docstring); `diagnosed_seq` is the `observation.diagnosed` it rests on."""

    line_id: str
    mark: Mark | None
    diagnosed_seq: int | None = None
    method: Literal["symbolic", "model"] | None = None


async def canvas_lines(db: Db, *, learner_id: UUID, canvas_id: UUID) -> list[CanvasLineCommitted]:
    """The lines of the canvas as they stand now, in the order they first appeared: the last
    commit of each, the learner's correction applied, the erased ones left out (Т-§15.1)."""
    rows = await db.fetch_all(_LINES, {"learner_id": learner_id, "canvas_id": str(canvas_id)})
    lines: dict[str, CanvasLineCommitted] = {}
    for row in rows:
        payload = row["payload"]
        if row["type"] == "canvas.line_committed":
            line = CanvasLineCommitted.model_validate(payload)
            lines[line.line_id] = line
        elif row["type"] == "canvas.line_erased":
            lines.pop(payload["line_id"], None)
        else:
            fix = HandwritingCorrected.model_validate(payload)
            if fix.line_id in lines:
                lines[fix.line_id] = lines[fix.line_id].model_copy(
                    update={"text": fix.text, "sympy": fix.sympy}
                )
    return list(lines.values())


def line_digest(line: CanvasLineCommitted) -> str:
    """The text of a line in a key: another reading of the line is another check."""
    return hashlib.sha256(f"{line.text}\n{line.sympy}".encode()).hexdigest()[:16]


def _math(line: CanvasLineCommitted) -> MathInput | None:
    """The line as compute reads it (Т-§22.7): `sympy` first, else LaTeX; a text line that
    has no `sympy` is not math."""
    if line.sympy:
        return MathInput(latex=line.text if line.kind == "latex" else None, sympy=line.sympy)
    if line.kind == "latex" and line.text.strip():
        return MathInput(latex=line.text)
    return None


def _numeric(settings: Settings) -> NumericParams:
    return NumericParams(
        numeric_points=settings.numeric_points,
        simplify_share=settings.math_simplify_share,
        precision_digits=settings.math_precision_digits,
        sample_low=settings.math_sample_low,
        sample_high=settings.math_sample_high,
        positive_sample_min=settings.math_positive_sample_min,
        max_attempts_per_point=settings.math_max_attempts_per_point,
        rel_tol=settings.math_rel_tol,
        abs_tol=settings.math_abs_tol,
    )


async def symbolic_transition(
    compute: ComputeClient,
    settings: Settings,
    previous: CanvasLineCommitted,
    line: CanvasLineCommitted,
) -> bool | None:
    """Does `line` follow from `previous`? `None` — the check is impossible (a line does not
    parse, compute failed or is down): the model takes over (Т-§15.4 п.3)."""
    before, after = _math(previous), _math(line)
    if before is None or after is None:
        return None
    request = TransitionRequest(
        previous=before,
        current=after,
        numeric=_numeric(settings),
        timeout_ms=settings.symbolic_inline_timeout_ms,
    )
    try:
        response = await compute.math_transition(request)
    except ComputeUnavailableError as exc:
        log.warning("canvas_transition_unavailable", error=repr(exc))
        return None
    if response.status != "completed":
        return None
    return response.follows


# --- context of the task -----------------------------------------------------------------


@dataclass(frozen=True)
class TaskContext:
    document: TaskDocument
    skills: list[UUID]
    memory: MemorySlice | None


async def task_context(env: StepEnv) -> TaskContext | None:
    """The task of the canvas with the skills it tests and what memory knows about them;
    `None` — the canvas has no task of the learner. Memory failing is not a failure of the
    check: the diagnoser works without known misconceptions."""
    if env.task_id is None:
        return None
    document = await get_task(env.db, learner_id=env.learner_id, task_id=env.task_id)
    if document is None:
        return None
    created = await task_events(
        env.db, learner_id=env.learner_id, task_ids=[env.task_id], types=("task.created",)
    )
    skills = list(
        dict.fromkeys(
            test.skill_id
            for event in created
            for test in TaskCreated.model_validate(event.payload).markup.tests
            if test.skill_id is not None
        )
    )
    memory: MemorySlice | None = None
    if env.reader is not None and skills:
        try:
            memory = await env.reader.slice_for_turn(
                env.learner_id, env.session_id or _NO_SESSION, SliceFocus(skill_ids=skills)
            )
        except Exception as exc:  # memory is an enrichment, never a reason to skip the check
            log.warning("canvas_memory_unavailable", error=repr(exc))
    return TaskContext(document, skills, memory)


async def solution_space_of(env: StepEnv) -> SolutionSpace | None:
    """The stored solution space of the canvas task, without reading memory."""
    if env.task_id is None:
        return None
    document = await get_task(env.db, learner_id=env.learner_id, task_id=env.task_id)
    return None if document is None else document.solution_space


def _labels(env: StepEnv, context: TaskContext) -> LabelMap:
    known = (
        [m.misconception_id for m in context.memory.misconceptions if m.skill_id in context.skills]
        if context.memory
        else []
    )
    return LabelMap.build(env.settings, skills=context.skills, misconceptions=known)


def _problem(document: TaskDocument) -> str:
    condition = document.condition
    return condition.text if not condition.latex else f"{condition.text}\n{condition.latex}"


def _space_block(space: SolutionSpace | None, labels: LabelMap) -> str:
    if space is None:
        return NONE
    steps = [
        f"{i}. {step.statement}" + (f" ({step.expression.latex})" if step.expression else "")
        for i, step in enumerate(space.key_steps, 1)
    ]
    wrong: list[str] = []
    for branch in space.wrong_branches:
        label = (
            labels.label("misconception", UUID(branch.misconception))
            if branch.misconception
            else None
        )
        tail = f", {label}" if label else ""
        wrong.append(f"- {branch.sign} ({branch.gap_type}{tail})")
    return "Key steps:\n" + "\n".join(steps) + "\nTypical wrong branches:\n" + "\n".join(wrong)


def _skills_block(labels: LabelMap, context: TaskContext) -> str:
    names = {s.skill_id: s.name for s in context.memory.skills} if context.memory else {}
    lines = []
    for label, skill_id in labels.labels("skill").items():
        name = names.get(skill_id)
        lines.append(f"{label}: {name}" if name else label)
    return "\n".join(lines) or NONE


def _misconceptions_block(labels: LabelMap, context: TaskContext) -> str:
    if context.memory is None:
        return NONE
    statements = {m.misconception_id: m.statement for m in context.memory.misconceptions}
    lines = [f"{label}: {statements[mid]}" for label, mid in labels.labels("misconception").items()]
    return "\n".join(lines) or NONE


def _earlier(lines: Sequence[CanvasLineCommitted]) -> str:
    kept = [line.text for line in lines if not line.crossed_out]
    return "\n".join(f"{i}. {text}" for i, text in enumerate(kept, 1)) or NONE


def _prompt_version(settings: Settings, role: str) -> int:
    prompt = settings.llm_roles[role].prompt
    if prompt is None:
        raise ValueError(f"role {role!r} has no prompt")
    return int(prompt.rsplit("@", 1)[1])


def _label_check(labels: LabelMap) -> Callable[[CanvasDiagnoserOutput], str | None]:
    def check(output: CanvasDiagnoserOutput) -> str | None:
        unknown: list[str] = []
        for kind, label in (("misconception", output.misconception), ("skill", output.root_cause)):
            if label is not None:
                try:
                    labels.resolve(kind, label)  # type: ignore[arg-type]
                except UnknownLabelError:
                    unknown.append(label)
        return (
            f"unknown labels: {', '.join(unknown)}; use only the given labels" if unknown else None
        )

    return check


def _record(
    output: CanvasDiagnoserOutput, labels: LabelMap, settings: Settings
) -> tuple[DiagnosisRecord, float] | None:
    """`diagnosis_record`; a label still unknown after the repair of the gateway is dropped,
    not trusted (D-C-136)."""
    try:
        return diagnosis_record(output, labels, settings)
    except UnknownLabelError as exc:
        field = "misconception" if exc.kind == "misconception" else "root_cause"
        return _record(output.model_copy(update={field: None}), labels, settings)


async def diagnose(
    env: StepEnv,
    context: TaskContext,
    line: CanvasLineCommitted,
    earlier: Sequence[CanvasLineCommitted],
) -> tuple[DiagnosisRecord, float] | None:
    """One call of `canvas_diagnoser@1`; `None` — no error found, or the model is down (then
    nothing is written and no mark is shown: a missed error costs less than a false one)."""
    labels = _labels(env, context)
    variables = {
        "problem": _problem(context.document),
        "solution_space": _space_block(context.document.solution_space, labels),
        "skills": _skills_block(labels, context),
        "known_misconceptions": _misconceptions_block(labels, context),
        "earlier_lines": _earlier(earlier),
        "line": line.text,
    }
    try:
        result = await env.llm.generate_structured(
            DIAGNOSER_ROLE,
            _prompt_version(env.settings, DIAGNOSER_ROLE),
            CanvasDiagnoserOutput,
            variables,
            env.learner_id,
            env.session_id,
            None,
            f"{DIAGNOSER_ROLE}:{env.canvas_id}:{line.line_id}:{line_digest(line)}",
            check=_label_check(labels),
        )
    except LLMError as exc:
        log.warning("canvas_diagnoser_failed", canvas_id=str(env.canvas_id), error=repr(exc))
        return None
    return _record(result.value, labels, env.settings)


_DIAGNOSED = (
    "SELECT seq, payload FROM core.events WHERE learner_id = %(learner_id)s "
    "AND type = 'observation.diagnosed' AND idempotency_key = %(key)s"
)


def diagnosed_key(env: StepEnv, line: CanvasLineCommitted) -> str:
    return f"observation.diagnosed:{env.canvas_id}:{line.line_id}:{line_digest(line)}"


def _mark_of(diagnosis: DiagnosisRecord, method: str) -> Mark | None:
    """What the board may show for a diagnosis (see the module docstring)."""
    if method == "symbolic":
        return "error"
    return "question" if diagnosis.kind == "question" else None


async def _written(env: StepEnv, line: CanvasLineCommitted, learner_id: UUID) -> StepOutcome | None:
    """The diagnosis of this reading of the line, if the log has one: a repeated check (the
    request "проверь", a reconnect) does not ask compute or the model again and cannot write a
    second diagnosis (law 5)."""
    row = await env.db.fetch_one(
        _DIAGNOSED, {"learner_id": learner_id, "key": diagnosed_key(env, line)}
    )
    if row is None:
        return None
    payload = ObservationDiagnosed.model_validate(row["payload"])
    return StepOutcome(
        line.line_id, _mark_of(payload.diagnosis, payload.method), row["seq"], payload.method
    )


async def _write_diagnosis(
    env: StepEnv,
    line: CanvasLineCommitted,
    diagnosis: DiagnosisRecord,
    confidence: float,
    method: Literal["symbolic", "model"],
) -> int:
    [stored] = await append_events(
        env.db,
        env.learner_id,
        [
            NewEvent(
                type="observation.diagnosed",
                payload={
                    "canvas_id": str(env.canvas_id),
                    "line_id": line.line_id,
                    "task_id": str(env.task_id) if env.task_id else None,
                    "diagnosis": diagnosis.model_dump(mode="json"),
                    "confidence": confidence,
                    "method": method,
                },
                actor="code" if method == "symbolic" else f"{DIAGNOSER_ROLE}@1",
                idempotency_key=diagnosed_key(env, line),
                session_id=env.session_id,
            )
        ],
        redis=env.redis,
    )
    return stored.seq


async def check_step(
    env: StepEnv, line: CanvasLineCommitted, earlier: Sequence[CanvasLineCommitted]
) -> StepOutcome:
    """The cascade for `line` after the lines `earlier` (unerased, in order). A line that is
    crossed out, or a canvas without a task of the learner, is not checked."""
    nothing = StepOutcome(line.line_id, None)
    if line.crossed_out:
        return nothing
    context = await task_context(env)
    if context is None:
        return nothing
    if (written := await _written(env, line, env.learner_id)) is not None:
        return written
    usable = [p for p in earlier if not p.crossed_out]
    previous = usable[-1] if usable else None
    follows = (
        None
        if previous is None
        else await symbolic_transition(env.compute, env.settings, previous, line)
    )
    if follows is True:
        return nothing  # a confirmed step is no error and no evidence (Т-§15.4 п.1)
    named = await diagnose(env, context, line, usable)
    if follows is False:
        # Confirmed by code. The model names the gap; if it cannot, the error stays.
        base = named[0] if named else None
        diagnosis = DiagnosisRecord(
            kind="error",
            gap_type=base.gap_type if base else SYMBOLIC_FALLBACK_GAP,
            misconception_id=base.misconception_id if base else None,
            misconception_hypothesis=base.misconception_hypothesis if base else None,
            root_cause_skill_id=base.root_cause_skill_id if base else None,
        )
        seq = await _write_diagnosis(env, line, diagnosis, 1.0, "symbolic")
        return StepOutcome(line.line_id, "error", seq, "symbolic")
    if named is None:
        return nothing
    diagnosis, confidence = named
    seq = await _write_diagnosis(env, line, diagnosis, confidence, "model")
    return StepOutcome(line.line_id, _mark_of(diagnosis, "model"), seq, "model")


# --- second opinion -----------------------------------------------------------------------


async def second_opinion(
    env: StepEnv,
    line: CanvasLineCommitted,
    outcome: StepOutcome,
    earlier: Sequence[CanvasLineCommitted],
) -> bool | None:
    """The second opinion on an unsure diagnosis (`second_opinion@1`, P1): `True` — the line
    holds an error (`outcome = incorrect`), `False` — it does not (`correct`, `partial`,
    `unclear`: a false diagnosis is worse than a missed one, `Т-§7.7`); `None` — the model is
    down, the mark stays a question. Written as `observation.second_opinion` under a key of the
    diagnosis, so the job of the dispatcher (`second_opinion.diagnosis`) repeats nothing."""
    if outcome.diagnosed_seq is None:
        return None
    context = await task_context(env)
    if context is None:
        return None
    labels = _labels(env, context)
    variables = {
        "problem": _problem(context.document),
        "rubric": NONE,
        "answer_spec": context.document.answer_spec.model_dump_json(exclude_none=True),
        "known_misconceptions": _misconceptions_block(labels, context),
        "learner_answer": (
            f"Earlier lines:\n{_earlier([p for p in earlier if not p.crossed_out])}\n"
            f"The line to judge:\n{line.text}"
        ),
    }
    try:
        result = await env.llm.generate_structured(
            SECOND_OPINION_ROLE,
            _prompt_version(env.settings, SECOND_OPINION_ROLE),
            ModelVerdictOutput,
            variables,
            env.learner_id,
            env.session_id,
            None,
            f"{SECOND_OPINION_ROLE}:{env.canvas_id}:{line.line_id}:{line_digest(line)}",
        )
    except LLMError as exc:
        log.warning("canvas_second_opinion_failed", canvas_id=str(env.canvas_id), error=repr(exc))
        return None
    agreed = result.value.outcome == "incorrect"
    await append_events(
        env.db,
        env.learner_id,
        [
            NewEvent(
                type="observation.second_opinion",
                payload={"target_seq": outcome.diagnosed_seq, "agreed": agreed},
                actor=f"{SECOND_OPINION_ROLE}@1",
                idempotency_key=f"observation.second_opinion:{outcome.diagnosed_seq}",
                session_id=env.session_id,
                causation_seq=outcome.diagnosed_seq,
            )
        ],
        redis=env.redis,
    )
    return agreed
