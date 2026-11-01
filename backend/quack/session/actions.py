"""Learner actions of `POST /v1/sessions/{id}/actions` and the SSE frames of the answer
(Т-§1.4, Т-§2.2, Т-§12.2, Т-§12.3, Т-§12.7; ТЗ Ф4 §4.7, WP13.1, seam S11).

One action per request, discriminated by `kind`, with the client's `idempotency_key`
(Т-§2.3). Actions with a teacher turn answer with SSE frames in the order of §4.7;
`confidence` answers `201 {seq}` without SSE. Payload field names follow the catalog
(ТЗ Ф4 §6.3).
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from quack.practice.answer_spec import LearnerAnswer
from quack.session.elements import Element

type AnswerSource = Literal["chat", "canvas", "photo", "card"]
type Confidence = Annotated[int, Field(ge=1, le=4)]


class _Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    idempotency_key: UUID


class CanvasRef(BaseModel):
    """A canvas line or block picked by the lasso (Т-§15.6)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canvas_id: UUID
    line_id: str | None = None
    block_id: str | None = None


class MessageAction(_Action):
    """`message.sent`: text, attached materials, canvas references, `/skill` call (Т-§17.8)."""

    kind: Literal["message"]
    text: str
    material_ids: list[UUID] = []
    canvas_refs: list[CanvasRef] = []
    skill_invocation: str | None = None


class IntentAction(_Action):
    """«Изучать / Практиковать / Повторять» on a topic or a hub (Т-§12.7) →
    `message.sent {intent, topic_id | hub_id}` (ТЗ Ф4 WP13.1)."""

    kind: Literal["intent"]
    intent: Literal["learn", "practice", "review"]
    topic_id: UUID | None = None
    hub_id: UUID | None = None


class AnswerAction(_Action):
    """`answer.submitted` (+ instant verdict in the same transaction, Т-§6.1)."""

    kind: Literal["answer"]
    task_id: UUID
    part: str | None = None
    answer: LearnerAnswer
    text: str | None = None
    confidence: Confidence | None = None
    element_id: str | None = None
    source: AnswerSource = "chat"
    # Т-§14.3, Т-§17.5: a «recall and reveal» card — «I remembered» yes / no (D-B-112).
    self_assessment: bool | None = None
    solve_time_ms: int | None = None


class ElementResponseAction(_Action):
    """`element.responded` (+ `answer.submitted` if the element is a task)."""

    kind: Literal["element_response"]
    turn_seq: int
    element_id: str
    response: str
    answer: LearnerAnswer | None = None
    text: str | None = None
    confidence: Confidence | None = None


class HintRequestAction(_Action):
    kind: Literal["hint_request"]
    task_id: UUID


class SolutionRequestAction(_Action):
    kind: Literal["solution_request"]
    task_id: UUID


class CheckSkipAction(_Action):
    kind: Literal["check_skip"]
    task_id: UUID


class FlagUnclearAction(_Action):
    """`item.flagged_unclear {turn_seq, block_id, quote}` (Т-§12.3)."""

    kind: Literal["flag_unclear"]
    turn_seq: int
    block_id: str
    quote: str


class ConfidenceAction(_Action):
    """`confidence.reported {target_seq, value}`: no teacher turn (`201`)."""

    kind: Literal["confidence"]
    target_seq: int
    value: Confidence


type Action = Annotated[
    MessageAction
    | IntentAction
    | AnswerAction
    | ElementResponseAction
    | HintRequestAction
    | SolutionRequestAction
    | CheckSkipAction
    | FlagUnclearAction
    | ConfidenceAction,
    Field(discriminator="kind"),
]

# ТЗ Ф4 WP13.1: kinds that start a teacher turn; the others answer `201 {seq}`.
TURN_ACTION_KINDS: frozenset[str] = frozenset(
    {
        "message",
        "intent",
        "answer",
        "element_response",
        "hint_request",
        "solution_request",
        "check_skip",
        "flag_unclear",
    }
)

# ТЗ Ф4 WP13.1: action kind → event type written for it.
ACTION_EVENT_TYPES: dict[str, str] = {
    "message": "message.sent",
    "intent": "message.sent",
    "answer": "answer.submitted",
    "element_response": "element.responded",
    "hint_request": "hint.requested",
    "solution_request": "solution.requested",
    "check_skip": "check.skipped",
    "flag_unclear": "item.flagged_unclear",
    "confidence": "confidence.reported",
}


# --- SSE frames (ТЗ Ф4 §4.7) ---


class _Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VerdictFrame(_Frame):
    """`verdict`: a fast verdict that came during the turn (Т-§6.1)."""

    answer_seq: int
    part: str
    outcome: Literal["correct", "incorrect", "partial", "unclear"]
    method: Literal["exact", "numeric", "symbolic", "units", "model"]
    verification: Literal["code", "model"]


class ActionRecordedFrame(_Frame):
    """`action_recorded {seq, verdict?}`: right after the action event is committed."""

    seq: int
    verdict: VerdictFrame | None = None


class StatusFrame(_Frame):
    """`status`: `thinking` after admission; `tool` with the tool name on each tool call."""

    state: Literal["thinking", "tool"]
    tool: str | None = None


class TokenFrame(_Frame):
    """`token {text}`: checked body text (Т-§12.2 этап 3)."""

    text: str


class ResetFrame(_Frame):
    """`reset {reason}`: the shown part is replaced by «уточняю…» (Т-§12.2 этап 3)."""

    reason: Literal["spoiler", "guard"]


class ElementFrame(_Frame):
    """`element {block_id, element}`: after its markup passed the code check."""

    block_id: str
    element: Element


class TurnCommittedFrame(_Frame):
    """`turn_committed {seq, degraded_mode, effort, role}` after `turn.committed`."""

    seq: int
    degraded_mode: (
        Literal[
            "budget_exceeded",
            "llm_unavailable",
            "memory_unavailable",
            "teacher_fallback",
            "projector_lag",
        ]
        | None
    )
    effort: Literal["low", "high"]
    role: Literal["teacher", "teacher_fallback"]


class ErrorFrame(_Frame):
    """`error {code, message, retryable}`: an error before the write — no turn in the log."""

    code: str
    message: str
    retryable: bool


# Event names of the frames, in the order of §4.7.
FRAME_EVENTS: dict[type[_Frame], str] = {
    ActionRecordedFrame: "action_recorded",
    StatusFrame: "status",
    VerdictFrame: "verdict",
    TokenFrame: "token",
    ResetFrame: "reset",
    ElementFrame: "element",
    TurnCommittedFrame: "turn_committed",
    ErrorFrame: "error",
}


class ActionAccepted(BaseModel):
    """`201 {seq}` of an action without a teacher turn."""

    seq: int
