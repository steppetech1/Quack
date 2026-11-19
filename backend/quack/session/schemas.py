"""Request/response models of the session endpoints (ТЗ §3) and LLM output schemas (ТЗ §4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.events.payloads import (
    ExtractedMisconception,
    ExtractedObservation,
    Unit,
)
from quack.session.sessions import FeedEvent, SessionView


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- HTTP responses (ТЗ Ф4 WP13.10; the request of the start is `sessions.StartSession`) ---


class SessionListResponse(StrictModel):
    sessions: list[SessionView]


class SessionDetail(SessionView):
    """`GET /v1/sessions/{id}`: the session and its feed of events by `seq` (D-B-210)."""

    events: list[FeedEvent]


class SessionJob(StrictModel):
    """A row of the learner's plaque (Т-§13.8): a visible job of the session."""

    job_key: str
    kind: str
    status: str  # `queued`, `running`, or (a few minutes after the end) `succeeded` / `failed`
    created_at: datetime


class SessionJobsResponse(StrictModel):
    jobs: list[SessionJob]


class HeartbeatResponse(StrictModel):
    status: Literal["ok"]
    ttl_sec: int


# --- LLM output schemas (ТЗ §4) ---


class ConceptExplainedDraft(StrictModel):
    """Labels of the call (WP14.1): `topic_label` `T…`, `explained_skill_labels` `S…`."""

    topic_label: str
    explained_skill_labels: list[str]
    depth: Unit
    summary: str


class TeacherOutput(StrictModel):
    """teacher@1. The model sees and writes labels, never ids (WP14.1, Т-§4.5); code
    resolves them (`quack.session.teacher_labels`)."""

    reply_text: str
    pedagogical_move: Literal[
        "explain", "socratic_question", "assign_task", "give_feedback", "summarize"
    ]
    focus_skill_labels: list[str] = []
    concept_explained: ConceptExplainedDraft | None = None
    assign_task_label: str | None = None
    evaluated_is_correct: bool | None = None
    evaluated_score: float | None = Field(default=None, ge=0.0, le=1.0)
    evaluated_feedback: str | None = None


class ExtractorOutput(StrictModel):
    observations: list[ExtractedObservation]
    misconceptions: list[ExtractedMisconception] = []


class ExtractorEntry(StrictModel):
    """One entry of `extractor@2` in the contract of its prompt (D-B-011): an observation
    about a skill (`kind = "skill_observation"`) or a suspected misconception
    (`kind = "misconception_suspicion"`); fields of the other kind are null. Code turns it
    into the element of WP12.1 (`quack.session.extraction`)."""

    kind: Literal["skill_observation", "misconception_suspicion"]
    skill: str
    misconception: str | None
    level: int | None = Field(ge=1, le=6)
    outcome: Literal["success", "failure", "bypass"] | None
    help: (
        Literal[
            "none",
            "hint_direction",
            "hint_key_step",
            "hint_near_solution",
            "after_example",
            "solution_shown",
        ]
        | None
    )
    gap_type: (
        Literal[
            "no_idea",
            "not_recognizing_when",
            "confuses_similar",
            "execution_error",
            "slip",
            "misconception",
        ]
        | None
    )
    quote: str
    events: list[int]


class ExtractorOutputV2(StrictModel):
    """extractor@2 (WP12.1, D-B-011)."""

    entries: list[ExtractorEntry]
