"""Request/response models of the session endpoints (ТЗ §3) and LLM output schemas (ТЗ §4)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from quack.events.payloads import (
    ExtractedMisconception,
    ExtractedObservation,
    SkillRef,
    Unit,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- HTTP requests ---


class StartSessionRequest(StrictModel):
    domain_id: UUID
    topic_id: UUID | None = None
    mode: Literal["learn", "practice", "review"] = "learn"
    goal: str | None = None
    plan: list[dict[str, Any]] | None = None
    idempotency_key: str


class FinishSessionRequest(StrictModel):
    idempotency_key: str


class TurnRequest(StrictModel):
    text: str
    related_task_id: UUID | None = None
    hints_used: int = 0
    response_time_sec: float | None = Field(default=None, ge=0.0)
    idempotency_key: str


# --- HTTP responses ---


class SessionStarted(StrictModel):
    session_id: UUID
    domain_id: UUID
    topic_id: UUID | None
    mode: str
    status: Literal["active"]
    plan: list[dict[str, Any]]
    start_seq: int


class ActiveSession(StrictModel):
    session_id: UUID
    domain_id: UUID
    topic_id: UUID | None
    mode: str
    status: str
    started_at: datetime
    last_activity_at: datetime
    last_turn_index: int
    last_checkpoint_turn: int


class TurnView(StrictModel):
    turn_index: int
    turn_id: UUID
    role: Literal["student", "teacher"]
    text: str
    seq: int
    related_task_id: UUID | None = None
    pedagogical_move: str | None = None
    degraded_mode: str | None = None


class CheckpointView(StrictModel):
    up_to_turn_index: int
    rolling_summary: str
    open_threads: list[str]
    key_observations: list[str]


class SessionDetail(ActiveSession):
    turns: list[TurnView]
    checkpoint: CheckpointView | None = None


class HeartbeatResponse(StrictModel):
    status: Literal["ok"]
    ttl_sec: int


class FinishResponse(StrictModel):
    session_id: UUID
    status: Literal["completed"]
    summary_stats: dict[str, Any]
    end_seq: int


# --- LLM output schemas (ТЗ §4) ---


class ConceptExplainedDraft(StrictModel):
    topic_id: UUID
    skills: list[SkillRef]
    depth: Unit
    summary: str


class TeacherOutput(StrictModel):
    reply_text: str
    pedagogical_move: Literal[
        "explain", "socratic_question", "assign_task", "give_feedback", "summarize"
    ]
    focus_skill_ids: list[UUID] = []
    concept_explained: ConceptExplainedDraft | None = None
    assign_task_id: UUID | None = None
    evaluated_is_correct: bool | None = None
    evaluated_score: float | None = Field(default=None, ge=0.0, le=1.0)
    evaluated_feedback: str | None = None


class ExtractorOutput(StrictModel):
    observations: list[ExtractedObservation]
    misconceptions: list[ExtractedMisconception] = []


class CheckpointerOutput(StrictModel):
    rolling_summary: str
    open_threads: list[str]
    key_observations: list[str]
