"""Registry of the 36 hackathon event types (spec §5), keyed by (type, schema_version)."""

from __future__ import annotations

from typing import Any

from quack.events import payloads as p

SCHEMA_VERSION = 1

EVENT_TYPES: dict[str, type[p.Payload]] = {
    # 1. Learner and domains
    "learner.registered": p.LearnerRegistered,
    "learner.preferences_updated": p.LearnerPreferencesUpdated,
    "domain.created": p.DomainCreated,
    "domain.archived": p.DomainArchived,
    # 2. Structure
    "structure.interview_completed": p.StructureInterviewCompleted,
    "structure.proposed": p.StructureProposed,
    "structure.confirmed": p.StructureConfirmed,
    "structure.edited": p.StructureEdited,
    "topic.status_changed": p.TopicStatusChanged,
    "topic.merged": p.TopicMerged,
    "skill.promoted": p.SkillPromoted,
    "skill.merged": p.SkillMerged,
    # 3. Diagnostics
    "diagnostic.started": p.DiagnosticStarted,
    "diagnostic.question_answered": p.DiagnosticQuestionAnswered,
    "diagnostic.completed": p.DiagnosticCompleted,
    # 4. Sessions and turns
    "session.started": p.SessionStarted,
    "session.completed": p.SessionCompleted,
    "session.abandoned": p.SessionAbandoned,
    "session.checkpointed": p.SessionCheckpointed,
    "turn.student_replied": p.TurnStudentReplied,
    "turn.teacher_replied": p.TurnTeacherReplied,
    "teacher.concept_explained": p.TeacherConceptExplained,
    "turn.extracted": p.TurnExtracted,
    # 5. Practice and tasks
    "task.generated": p.TaskGenerated,
    "task.assigned": p.TaskAssigned,
    "task.hint_given": p.TaskHintGiven,
    "task.submitted": p.TaskSubmitted,
    "task.evaluated": p.TaskEvaluated,
    # 6. Misconceptions and trace revision
    "misconception.resolved": p.MisconceptionResolved,
    "misconception.merged": p.MisconceptionMerged,
    "evidence.superseded": p.EvidenceSuperseded,
    # 7. Artifacts
    "session.digest_created": p.SessionDigestCreated,
    "artifact.note_created": p.ArtifactNoteCreated,
    "artifact.note_updated": p.ArtifactNoteUpdated,
    "artifact.note_deleted": p.ArtifactNoteDeleted,
    "artifact.cheatsheet_updated": p.ArtifactCheatsheetUpdated,
}

# Events whose payload has a searchable `text` field (spec §3.2, §5.2 note).
TEXT_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "turn.student_replied",
        "turn.teacher_replied",
        "artifact.note_created",
        "artifact.note_updated",
        "artifact.cheatsheet_updated",
        "session.digest_created",
    }
)

# Session events read by build_session_overlay (spec §8.2).
OVERLAY_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "turn.student_replied",
        "turn.teacher_replied",
        "task.assigned",
        "task.hint_given",
        "task.submitted",
        "task.evaluated",
        "turn.extracted",
    }
)


# Newer schema versions of existing types (ТЗ Фазы 3 §6.8); older versions stay readable.
EVENT_TYPE_VERSIONS: dict[tuple[str, int], type[p.Payload]] = {
    **{(event_type, SCHEMA_VERSION): model for event_type, model in EVENT_TYPES.items()},
    ("turn.extracted", 2): p.TurnExtractedV2,
}


class UnknownEventTypeError(ValueError):
    """Unknown type or newer schema_version than this code knows (Т-§2.4)."""


def payload_model(event_type: str, schema_version: int = SCHEMA_VERSION) -> type[p.Payload]:
    model = EVENT_TYPE_VERSIONS.get((event_type, schema_version))
    if model is None:
        raise UnknownEventTypeError(f"unknown event type {event_type!r} v{schema_version}")
    return model


def validate_payload(
    event_type: str, payload: dict[str, Any] | p.Payload, schema_version: int = SCHEMA_VERSION
) -> p.Payload:
    model = payload_model(event_type, schema_version)
    if isinstance(payload, p.Payload):
        if not isinstance(payload, model):
            raise TypeError(f"payload for {event_type!r} must be {model.__name__}")
        return payload
    return model.model_validate(payload)
