"""Model verdicts and the second opinion (Т-§6.8, Т-§7.7; ТЗ Ф4 WP11.6).

The body of the job `check.model` (rules — seam S6): `check_with_model` calls `verdict_proof`
or `verdict_free` with the condition, the rubric, the spec, the known misconceptions (labels
`M…`) and the answer; turns the output into a `Verdict`; asks `needs_second_opinion` with the
state of the tested levels (`quack.graph.read`, seam S4) and, if so, calls `second_opinion`;
`record_verdict` writes `verdict.recorded` under `verdict:{answer_seq}:{part}`. The tier (Т2
agreed, Т3 not) is set by code from `secondOpinion` (WP4.1, stream A), never here (law 4).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from uuid import UUID

from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AnswerSubmitted
from quack.events.keys import verdict_key
from quack.events.log import NewEvent, append_events
from quack.graph.read.models import MisconceptionFact
from quack.llm.gateway import LLMGateway
from quack.practice.answer_spec import AnswerSpec, AnswerType
from quack.practice.check import (
    HelpUsed,
    RubricItemResult,
    SecondOpinion,
    Verdict,
    apply_reading,
)
from quack.practice.role_outputs import ModelVerdictOutput, SolutionSpace, TaskCondition


@dataclass(frozen=True)
class LevelState:
    """State of one level the task tests (`skill`, `level` of its markup), read from memory:
    proven by a direct Т1 or Т2; the latest Т1 of this level by time is a failure
    [РЕШЕНИЕ Ф4 WP11.6]."""

    proven_by_direct: bool
    last_t1_failed: bool


def model_verdict(
    output: ModelVerdictOutput,
    *,
    answer_seq: int,
    part: str,
    help_used: HelpUsed | None = None,
    known_misconceptions: Sequence[UUID] = (),
) -> Verdict:
    """`verdict.recorded` of a model verdict (Т-§6.8, WP11.4): class model, rubric items with
    quotes, confidence, a misconception id or hypothesis. `known_misconceptions` lists the ids
    behind the labels `M1`... in the order the prompt showed them; the event never carries a
    label (Т-§4.5, Q-A-2 п.1), an unknown label is dropped."""
    help_used = help_used or HelpUsed()
    misconception_id = _misconception_id(output.misconception, known_misconceptions)
    return Verdict(
        answer_seq=answer_seq,
        part=part,
        outcome=output.outcome,
        method="model",
        verification="model",
        confidence=output.confidence,
        help_step=help_used.hint_step,
        solution_shown=help_used.solution_requested,
        rubric=[
            RubricItemResult(item=i.item, status=i.status, quote=i.quote) for i in output.items
        ],
        misconception_id=misconception_id,
        misconception_hypothesis=None if misconception_id else output.misconception_hypothesis,
    )


def _misconception_id(label: str | None, known: Sequence[UUID]) -> UUID | None:
    if label is None:
        return None
    label = label.strip()
    if label.startswith("M") and label[1:].isdigit() and 1 <= int(label[1:]) <= len(known):
        return known[int(label[1:]) - 1]
    return None


def needs_second_opinion(
    verdict: Verdict, levels: Sequence[LevelState], settings: Settings
) -> bool:
    """Т-§7.7: only a model verdict and only in doubt — confidence below
    `verdict_min_confidence`, or `incorrect` on a level proven by a direct Т1/Т2, or `correct`
    on a level whose latest Т1 failed. `partial` contradicts neither (Т-§7.7, spike)."""
    if verdict.verification != "model":
        return False
    if verdict.confidence is not None and verdict.confidence < settings.verdict_min_confidence:
        return True
    if verdict.outcome == "incorrect":
        return any(level.proven_by_direct for level in levels)
    if verdict.outcome == "correct":
        return any(level.last_t1_failed for level in levels)
    return False


def with_second_opinion(first: Verdict, second: ModelVerdictOutput) -> Verdict:
    """The first verdict with `secondOpinion {requested: true, agreed}` (Т-§7.7): agreed —
    the same outcome."""
    agreed = second.outcome == first.outcome
    return first.model_copy(update={"second_opinion": SecondOpinion(requested=True, agreed=agreed)})


# --- the job body (WP11.6) ---


@dataclass(frozen=True)
class KnownMisconception:
    """A misconception of the tested skills shown to the model with a label `M…`."""

    misconception_id: UUID
    statement: str


def known_misconceptions(
    facts: Sequence[MisconceptionFact], *, skill_ids: Collection[UUID]
) -> list[KnownMisconception]:
    """The misconceptions of the tested skills from the slice (seam S4), with the statement
    the model sees as «M1. <statement>» (D-B-145), in the order of the slice."""
    wanted = set(skill_ids)
    return [
        KnownMisconception(f.misconception_id, f.statement) for f in facts if f.skill_id in wanted
    ]


def verdict_role(answer_type: AnswerType, *, failed: bool) -> str:
    """Т-§6.8, Т-§22.7: `proof` — `verdict_proof`; a short free answer, a drawing and a fast
    answer that did not check (`check.failed`) — `verdict_free`."""
    return "verdict_proof" if answer_type == "proof" and not failed else "verdict_free"


def verdict_inputs(
    condition: TaskCondition,
    spec: AnswerSpec,
    solution_space: SolutionSpace | None,
    answer: AnswerSubmitted,
    misconceptions: Sequence[KnownMisconception],
) -> dict[str, str]:
    """Input blocks of `verdict_*@1` and `second_opinion@1` (PROMPT_CONTRACTS.md): the rubric
    is the key steps of the solution space, else the key ideas of the spec (Т-§6.8). Rubric
    items are `R1…`, misconceptions `M1…` in the given order (D-B-145)."""
    points = (
        [step.statement for step in solution_space.key_steps]
        if solution_space is not None
        else list(spec.key_ideas or [])
    )
    problem = condition.text if condition.latex is None else f"{condition.text}\n{condition.latex}"
    learner = (
        answer.answer.value if answer.text is None else f"{answer.answer.value}\n{answer.text}"
    )
    return {
        "problem": problem,
        "rubric": _numbered("R", points),
        "answer_spec": spec.model_dump_json(exclude_none=True),
        "known_misconceptions": _numbered("M", [m.statement for m in misconceptions]),
        "learner_answer": learner,
    }


async def check_with_model(
    gateway: LLMGateway,
    settings: Settings,
    *,
    learner_id: UUID,
    session_id: UUID | None,
    condition: TaskCondition,
    spec: AnswerSpec,
    solution_space: SolutionSpace | None,
    answer: AnswerSubmitted,
    answer_seq: int,
    part: str,
    failed: bool,
    misconceptions: Sequence[KnownMisconception],
    levels: Sequence[LevelState],
    help_used: HelpUsed,
) -> Verdict:
    """One call of the verdict role per answer, a second opinion only in doubt (Т-§7.7)."""
    variables = verdict_inputs(condition, spec, solution_space, answer, misconceptions)
    rubric_size = len(variables["rubric"].splitlines()) if variables["rubric"] != NONE else 0
    label_check = _label_check(rubric_size, len(misconceptions))
    known = [m.misconception_id for m in misconceptions]
    role = verdict_role(spec.type, failed=failed)
    first = await gateway.generate_structured(
        role,
        _prompt_version(settings, role),
        ModelVerdictOutput,
        variables,
        learner_id,
        session_id,
        None,
        f"check.model:{answer_seq}:{part}",
        check=label_check,
    )
    verdict = model_verdict(
        first.value,
        answer_seq=answer_seq,
        part=part,
        help_used=help_used,
        known_misconceptions=known,
    )
    # The doubt of Т-§7.7 is the model's own; an unsure reading is answered by showing the
    # reading (WP11.7), not by a second model, so it applies last (D-B-145).
    if not needs_second_opinion(verdict, levels, settings):
        return apply_reading(verdict, answer.recognition_confidence, settings)
    second = await gateway.generate_structured(
        SECOND_OPINION_ROLE,
        _prompt_version(settings, SECOND_OPINION_ROLE),
        ModelVerdictOutput,
        variables,
        learner_id,
        session_id,
        None,
        f"{SECOND_OPINION_ROLE}:{answer_seq}:{part}",
        check=label_check,
    )
    verdict = with_second_opinion(verdict, second.value)
    return apply_reading(verdict, answer.recognition_confidence, settings)


async def record_verdict(
    db: Db, verdict: Verdict, *, learner_id: UUID, session_id: UUID | None, domain_id: UUID | None
) -> None:
    """`verdict.recorded` under `verdict:{answer_seq}:{part}`: a repeat writes nothing new."""
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="verdict.recorded",
                payload=verdict.model_dump(mode="json"),
                actor="code",
                idempotency_key=verdict_key(verdict.answer_seq, verdict.part),
                domain_id=domain_id,
                session_id=session_id,
                causation_seq=verdict.answer_seq,
            )
        ],
    )


SECOND_OPINION_ROLE = "second_opinion"
# The block of an empty list (no rubric, no known misconceptions).
NONE = "none"


def _numbered(prefix: str, points: Sequence[str]) -> str:
    if not points:
        return NONE
    return "\n".join(f"{prefix}{i}. {point}" for i, point in enumerate(points, 1))


def _prompt_version(settings: Settings, role: str) -> int:
    """The version of `role@N` in the role table (Т-§11.1, WP10.1)."""
    prompt = settings.llm_roles[role].prompt
    if prompt is None:
        raise ValueError(f"role {role!r} has no prompt")
    return int(prompt.rsplit("@", 1)[1])


def _label_check(
    rubric_size: int, misconceptions: int
) -> Callable[[ModelVerdictOutput], str | None]:
    """Т-§4.5: an unknown label goes back to the model (the gateway's repair path); what is
    still unknown after it, `model_verdict` drops."""
    rubric = {f"R{i}" for i in range(1, rubric_size + 1)}
    known = {f"M{i}" for i in range(1, misconceptions + 1)}

    def check(output: ModelVerdictOutput) -> str | None:
        errors = [f"unknown rubric item {i.item}" for i in output.items if i.item not in rubric]
        if output.misconception is not None and output.misconception not in known:
            errors.append(f"unknown misconception label {output.misconception}")
        return "; ".join(errors) or None

    return check
