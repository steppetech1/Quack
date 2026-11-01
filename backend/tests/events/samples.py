"""A valid payload of every catalog type (ТЗ Ф4 §6.3, §8.1 «фабрика событий каталога»).

Used by the catalog tests (T-EV*) and by projector and dispatcher tests of all streams:
`sample_payload(type)` returns a fresh dict that `validate_payload` accepts.
"""

from __future__ import annotations

import copy
from typing import Any
from uuid import UUID, uuid5

_NS = UUID("6f1f2b8e-7a54-4d1e-9a3b-0c2f4e5d6a7b")


def uid(name: str) -> str:
    """A stable UUID string per name, so samples are deterministic."""
    return str(uuid5(_NS, name))


_SPEC = {"type": "number", "value": {"latex": "2", "sympy": "2"}}
_MARKUP = {
    "tests": [{"skill_id": uid("skill"), "level": 3}],
    "method_named": False,
    "answer": _SPEC,
}
_HEAD = {
    "intent": "practice",
    "target_skill_ids": [uid("skill")],
    "verdict_stance": None,
    "branch_type": None,
    "approach": "attempt_first",
}
_TURN_MARKUP = {
    "tasks": [{**_MARKUP, "element_id": "e1", "task_id": uid("task")}],
    "elements": [
        {
            "element_id": "e1",
            "kind": "task",
            "prompt": "Solve",
            "canvas": False,
            "ask_confidence": True,
        }
    ],
    "exposures": [{"skill_id": uid("skill"), "kind": "explained", "approach": "small_steps"}],
    "targets_misconception_id": None,
    "plan_update": {
        "completed_steps": [],
        "current_step": "practice",
        "next_steps": ["review"],
        "branches": [],
        "cancel_request_ids": [],
    },
    "agent_requests": [],
    "corrections_acknowledged": [],
    "decision_reason": "first attempt",
    "deadline": None,
}
_ELEMENT = {
    "kind": "evidence",
    "skill_id": uid("skill"),
    "new_skill": None,
    "skill_unclear": False,
    "level": 3,
    "outcome": "correct",
    "help": "none",
    "hint_step": None,
    "gap_type": "none",
    "misconception_id": None,
    "new_misconception": None,
    "root_cause": None,
    "quote": "x = 2",
    "events": [5],
    "at_ms": 1_000,
}
_DIAGNOSIS = {
    "kind": "error",
    "gap_type": "execution",
    "misconception_id": None,
    "misconception_hypothesis": None,
    "root_cause_skill_id": None,
}

SAMPLES: dict[str, dict[str, Any]] = {
    "message.sent": {"text": "hello"},
    "answer.submitted": {
        "task_id": uid("task"),
        "answer": {"kind": "number", "value": "2"},
        "source": "chat",
    },
    "element.responded": {"turn_seq": 3, "element_id": "e1", "response": "A"},
    "canvas.line_committed": {
        "canvas_id": uid("canvas"),
        "line_id": "l1",
        "text": "x = 2",
        "kind": "formula",
        "bbox": [0.0, 0.0, 10.0, 2.0],
        "stroke_ids": ["s1"],
        "started_at_ms": 1_000,
        "ended_at_ms": 2_000,
        "source": "pen",
        "recognition_confidence": 0.9,
        "crossed_out": False,
    },
    "canvas.line_erased": {"canvas_id": uid("canvas"), "line_id": "l1", "text": "x = 2"},
    "voice.transcribed": {"text": "I think", "started_at_ms": 1_000, "ended_at_ms": 3_000},
    "photo.uploaded": {"photo_id": uid("photo"), "object_key": "learners/x/photos/p.jpg"},
    "confidence.reported": {"target_seq": 4, "value": 3},
    "hint.requested": {"task_id": uid("task")},
    "solution.requested": {"task_id": uid("task")},
    "check.skipped": {"task_id": uid("task")},
    "item.flagged_unclear": {"turn_seq": 3, "block_id": "b1", "text": "this step"},
    "prep_mode.enabled": {"hub_id": uid("hub")},
    "prep_mode.disabled": {"hub_id": uid("hub")},
    "grade.reported": {"hub_id": uid("hub"), "grade": "5", "scale": "1-5"},
    "turn.committed": {
        "action_seq": 2,
        "text": "Try this one.",
        "blocks": [{"block_id": "b1", "text": "Try this one.", "element_id": None}],
        "head": _HEAD,
        "markup": _TURN_MARKUP,
        "role": "teacher@2",
        "model": "muse-wonder-1.0",
        "effort": "low",
        "degraded_mode": None,
        "guard_retries": 0,
        "manifest_id": uid("manifest"),
    },
    "verdict.recorded": {
        "answer_seq": 5,
        "part": "main",
        "outcome": "correct",
        "method": "exact",
        "verification": "code",
    },
    "check.failed": {"answer_seq": 5, "part": "main", "reason": "unparsed"},
    "observation.extracted": {
        "episode_seq": 9,
        "extractor": "extractor@3",
        "trigger_seq": 9,
        "elements": [_ELEMENT],
    },
    "observation.diagnosed": {
        "canvas_id": uid("canvas"),
        "line_id": "l1",
        "task_id": uid("task"),
        "diagnosis": _DIAGNOSIS,
        "confidence": 0.7,
        "method": "model",
    },
    "observation.second_opinion": {"target_seq": 10, "agreed": True},
    "domain.created": {"domain_id": uid("domain"), "name": "Calculus"},
    "topic.proposed": {
        "topic_id": uid("topic"),
        "domain_id": uid("domain"),
        "name": "Limits",
        "origin": "material",
    },
    "topic.created": {
        "topic_id": uid("topic"),
        "domain_id": uid("domain"),
        "name": "Limits",
        "origin": "session",
    },
    "topic.merged": {"from_id": uid("topic2"), "into_id": uid("topic")},
    "topic.split": {
        "topic_id": uid("topic"),
        "into": [
            {"topic_id": uid("topic.a"), "name": "A", "skill_ids": [uid("skill")]},
            {"topic_id": uid("topic.b"), "name": "B", "skill_ids": []},
        ],
    },
    "topic.renamed": {"topic_id": uid("topic"), "name": "Limits of functions"},
    "topic.hidden": {"topic_id": uid("topic")},
    "skill.created": {
        "skill_id": uid("skill"),
        "topic_id": uid("topic"),
        "domain_id": uid("domain"),
        "name": "Limit by definition",
        "summary": "epsilon-delta",
        "status": "provisional",
        "origin": "session",
    },
    "skill.merged": {"from_id": uid("skill2"), "into_id": uid("skill")},
    "skill.split": {
        "skill_id": uid("skill"),
        "into": [
            {"skill_id": uid("skill.a"), "name": "A", "topic_id": uid("topic")},
            {"skill_id": uid("skill.b"), "name": "B", "topic_id": uid("topic")},
        ],
    },
    "skill.merge_suggested": {"from_id": uid("skill2"), "into_id": uid("skill")},
    "edge.added": {
        "kind": "requires",
        "from_id": uid("skill"),
        "to_id": uid("skill2"),
        "origin": "model",
        "confidence": 0.7,
    },
    "edge.removed": {"kind": "requires", "from_id": uid("skill"), "to_id": uid("skill2")},
    "hub.created": {
        "hub_id": uid("hub"),
        "domain_id": uid("domain"),
        "name": "Exam",
        "kind": "request",
        "origin": "learner",
        "control_type": "tasks",
    },
    "hub.deadline_set": {"hub_id": uid("hub"), "deadline_at_ms": 9_000_000, "source": "turn"},
    "hub.topics_changed": {
        "hub_id": uid("hub"),
        "topics": [{"topic_id": uid("topic"), "required_levels": [3, 4]}],
    },
    "task.created": {
        "task_id": uid("task"),
        "family_id": uid("family"),
        "source": "teacher",
        "markup": _MARKUP,
        "verification": "unchecked",
    },
    "task.verified": {"task_id": uid("task")},
    "task.key_disputed": {"task_id": uid("task")},
    "task.markup_disputed": {"task_id": uid("task"), "corrected_markup": _MARKUP},
    "material.uploaded": {
        "material_id": uid("material"),
        "object_key": "learners/x/materials/m.pdf",
        "mime": "application/pdf",
    },
    "material.parsed": {"material_id": uid("material"), "pages_count": 3},
    "chunk.indexed": {
        "chunk_id": uid("chunk"),
        "material_id": uid("material"),
        "page": 1,
        "text": "A limit is…",
        "notation": ["\\lim"],
        "teaches": [uid("skill")],
        "uses": [],
    },
    "dispute.filed": {"target_kind": "evidence", "target_id": uid("evidence")},
    "typo.declared": {"answer_seq": 5, "part": "main"},
    "known.claimed": {"topic_id": uid("topic")},
    "handwriting.corrected": {"canvas_id": uid("canvas"), "line_id": "l1", "text": "x = 3"},
    "episode.reassigned": {"episode_seq": 9, "topic_id": uid("topic")},
    "preference.set": {"key": "language", "value": "en"},
    "consent.given": {"kind": "terms", "version": "2026-11"},
    "consent.revoked": {"kind": "research", "version": "2026-11"},
    "session.started": {
        "session_id": uid("session"),
        "domain_id": uid("domain"),
        "intent": "learn",
    },
    "session.ended": {"session_id": uid("session"), "reason": "learner"},
    "hint.shown": {"task_id": uid("task"), "step": 1, "source": "teacher"},
    "today.shown": {
        "domain_id": uid("domain"),
        "items": [{"skill_id": uid("skill"), "reasons": ["due"], "score": 1.0}],
    },
    "task.closed": {"task_id": uid("task"), "how": "solved"},
    "episode.closed": {"from_seq": 1, "to_seq": 9, "has_content": True},
    "agent.requested": {
        "request_id": uid("request"),
        "kind": "viz",
        "params": {"skill_id": uid("skill"), "description": "plot of x^2"},
        "needed_in_session": True,
    },
    "agent.completed": {"request_id": uid("request"), "kind": "viz", "ok": True},
    "digest.written": {
        "domain_id": uid("domain"),
        "text": "Working on limits.",
        "as_of_seq": 20,
        "structured": {
            "frontier": [{"skill_id": uid("skill"), "level": 3, "state": "unstable"}],
            "gap_hypotheses": [],
            "uncertainty": None,
            "moves": [{"move": "practice", "reason": "unstable"}],
            "forecast": [],
        },
        "plan_divergence": False,
        "manner": "short steps",
    },
    "misconception.described": {
        "misconception_id": uid("misconception"),
        "statement": "limit equals the value",
        "triggers": ["continuity"],
    },
    "misconception.merged": {"from_id": uid("misconception2"), "into_id": uid("misconception")},
    "artifact.updated": {
        "artifact_id": uid("artifact"),
        "version": 1,
        "kind": "topic_notes",
        "topic_id": uid("topic"),
        "text": "Notes",
    },
    "embedding.ready": {
        "node_kind": "skill",
        "node_id": uid("skill"),
        "model": "qwen3-embedding-0.6b",
        "dim": 2,
        "vector": "AAAAAAAAAAA=",
    },
}


def sample_payload(event_type: str) -> dict[str, Any]:
    return copy.deepcopy(SAMPLES[event_type])
