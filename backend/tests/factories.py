"""Test helpers: building event histories without a database."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from quack.events.catalog import validate_payload
from quack.events.hashing import payload_hash
from quack.events.models import NewEvent, StoredEvent

T0 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=UTC)


def uid() -> UUID:
    return uuid4()


@dataclass
class EventLog:
    """Builds a gapless StoredEvent history for one learner."""

    learner_id: UUID = field(default_factory=uuid4)
    now: datetime = T0
    events: list[StoredEvent] = field(default_factory=list)

    def advance(self, **delta: float) -> None:
        self.now = self.now + timedelta(**delta)

    def add(
        self,
        type_: str,
        payload: dict[str, Any],
        *,
        actor: str = "system",
        session_id: UUID | None = None,
        domain_id: UUID | None = None,
        at: datetime | None = None,
        step_sec: float = 1.0,
        schema_version: int = 1,
    ) -> StoredEvent:
        model = validate_payload(type_, payload, schema_version)
        data = model.model_dump(mode="json")
        if at is not None:
            self.now = at
        else:
            self.now = self.now + timedelta(seconds=step_sec)
        if session_id is None and isinstance(data.get("session_id"), str):
            session_id = UUID(data["session_id"])
        if domain_id is None and isinstance(data.get("domain_id"), str):
            domain_id = UUID(data["domain_id"])
        seq = len(self.events) + 1
        event = StoredEvent(
            learner_id=self.learner_id,
            seq=seq,
            event_id=uuid4(),
            type=type_,
            schema_version=schema_version,
            actor=actor,
            session_id=session_id,
            turn_id=UUID(data["turn_id"]) if isinstance(data.get("turn_id"), str) else None,
            domain_id=domain_id,
            correlation_id=None,
            causation_id=None,
            idempotency_key=f"{type_}:{seq}:{uuid4()}",
            payload_hash=payload_hash(data),
            payload=data,
            occurred_at=self.now,
        )
        self.events.append(event)
        return event


def new_event(
    type_: str, payload: dict[str, Any], *, key: str | None = None, **kw: Any
) -> NewEvent:
    return NewEvent(
        type=type_,
        payload=payload,
        actor=kw.pop("actor", "system"),
        idempotency_key=key or f"{type_}:{uuid4()}",
        **kw,
    )


def sample_payloads() -> dict[str, dict[str, Any]]:
    """One valid payload per event type (36)."""
    d, t, s, k, m, sess, turn, task, art = (str(uid()) for _ in range(9))
    skill_ref = {"skill_id": s}
    new_skill_ref = {"new_skill": {"name": "Chain rule", "topic_id": t}}
    return {
        "learner.registered": {"email": "a@b.c", "display_name": "A", "preferences": {}},
        "learner.preferences_updated": {"preferences": {"pace": "slow", "daily_goal_min": 20}},
        "domain.created": {"domain_id": d, "name": "Calculus", "slug": "calculus"},
        "domain.archived": {"domain_id": d},
        "structure.interview_completed": {
            "domain_id": d,
            "goals": "exam",
            "background": "school",
            "focus_areas": ["limits"],
        },
        "structure.proposed": {
            "domain_id": d,
            "structure_version": 1,
            "topics": [{"topic_id": t, "name": "Limits"}],
            "skills": [{"skill_id": s, "topic_id": t, "name": "Limit laws"}],
            "prereqs": [],
            "skill_deps": [],
            "misconceptions": [{"misconception_id": m, "skill_id": s, "name": "Sign error"}],
        },
        "structure.confirmed": {"domain_id": d, "structure_version": 1},
        "structure.edited": {"domain_id": d, "structure_version": 2},
        "topic.status_changed": {"domain_id": d, "topic_id": t, "status": "skipped"},
        "topic.merged": {
            "domain_id": d,
            "source_topic_id": t,
            "target_topic_id": str(uid()),
            "reason": "dup",
        },
        "skill.promoted": {"domain_id": d, "skill_id": s, "topic_id": t, "name": "Chain rule"},
        "skill.merged": {
            "domain_id": d,
            "source_skill_id": s,
            "target_skill_id": k,
            "reason": "dup",
        },
        "diagnostic.started": {"domain_id": d, "session_id": sess, "candidate_topic_ids": [t]},
        "diagnostic.question_answered": {
            "domain_id": d,
            "session_id": sess,
            "turn_id": turn,
            "task_id": task,
            "topic_id": t,
            "skill_id": s,
            "answer": "2",
            "is_correct": True,
            "score": 1.0,
        },
        "diagnostic.completed": {
            "domain_id": d,
            "session_id": sess,
            "tested_topic_ids": [t],
            "inferred_priors": [
                {
                    "topic_id": t,
                    "skill_id": s,
                    "prior_mastery": 0.4,
                    "confidence": 0.5,
                    "source": "propagated",
                }
            ],
        },
        "session.started": {
            "session_id": sess,
            "domain_id": d,
            "topic_id": t,
            "mode": "learn",
            "goal": None,
            "plan": [],
        },
        "session.completed": {"session_id": sess, "domain_id": d, "summary_stats": {}},
        "session.abandoned": {
            "session_id": sess,
            "domain_id": d,
            "reason": "inactivity_timeout",
        },
        "session.checkpointed": {
            "session_id": sess,
            "up_to_turn_index": 5,
            "rolling_summary": "sum",
            "open_threads": [],
            "key_observations": [],
        },
        "turn.student_replied": {
            "session_id": sess,
            "turn_id": turn,
            "turn_index": 0,
            "text": "hi",
        },
        "turn.teacher_replied": {
            "session_id": sess,
            "turn_id": turn,
            "turn_index": 1,
            "text": "hello",
            "pedagogical_move": "explain",
        },
        "teacher.concept_explained": {
            "session_id": sess,
            "turn_id": turn,
            "topic_id": t,
            "skills": [skill_ref, new_skill_ref],
            "depth": 0.7,
            "summary": "chain rule",
        },
        "turn.extracted": {
            "session_id": sess,
            "turn_id": turn,
            "observations": [
                {
                    "skill": skill_ref,
                    "direction": "positive",
                    "strength": 0.6,
                    "independence": 1.0,
                    "tested_depth": 0.5,
                    "polarity_weight": 1.0,
                    "evidence_text": "it is 2",
                }
            ],
            "misconceptions": [
                {
                    "name": "Sign error",
                    "skill": skill_ref,
                    "topic_id": t,
                    "confidence": 0.7,
                    "evidence_text": "-2",
                }
            ],
        },
        "task.generated": {
            "task_id": task,
            "domain_id": d,
            "topic_id": t,
            "family_id": "limits-basic",
            "difficulty": 0.4,
            "kind": "math",
            "prompt_text": "lim x->0",
            "reference_solution": {"sympy": "0"},
            "target_skills": [{"skill": skill_ref, "weight": 1.0}],
        },
        "task.assigned": {"session_id": sess, "turn_id": turn, "task_id": task, "topic_id": t},
        "task.hint_given": {
            "session_id": sess,
            "turn_id": turn,
            "task_id": task,
            "hint_level": 1,
            "hint_text": "factor",
            "independence_penalty": 0.25,
        },
        "task.submitted": {
            "session_id": sess,
            "turn_id": turn,
            "task_id": task,
            "answer": "0",
            "hints_used": 1,
        },
        "task.evaluated": {
            "session_id": sess,
            "turn_id": turn,
            "task_id": task,
            "evaluator": "compute",
            "is_correct": True,
            "score": 1.0,
            "independence": 0.75,
            "feedback": "ok",
            "skill_scores": [{"skill_id": s, "score": 1.0, "tested_depth": 0.5}],
        },
        "misconception.resolved": {
            "domain_id": d,
            "misconception_id": m,
            "resolution_reason": "fixed",
        },
        "misconception.merged": {
            "domain_id": d,
            "source_misconception_id": m,
            "target_misconception_id": str(uid()),
            "reason": "dup",
        },
        "evidence.superseded": {"target_event_seq": 3, "reason": "re-evaluated"},
        "session.digest_created": {
            "artifact_id": art,
            "session_id": sess,
            "domain_id": d,
            "topic_id": t,
            "title": "Digest",
            "text": "we did limits",
            "covered_skill_ids": [s],
        },
        "artifact.note_created": {
            "artifact_id": art,
            "domain_id": d,
            "topic_id": None,
            "session_id": None,
            "title": "Note",
            "text": "my note",
        },
        "artifact.note_updated": {
            "artifact_id": art,
            "version": 2,
            "title": "Note",
            "text": "edited",
        },
        "artifact.note_deleted": {"artifact_id": art},
        "artifact.cheatsheet_updated": {
            "artifact_id": art,
            "domain_id": d,
            "topic_id": t,
            "version": 1,
            "title": "Cheatsheet",
            "text": "formulas",
        },
    }
