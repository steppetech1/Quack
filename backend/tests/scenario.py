"""A complex learner history touching all 36 event types, merges and superseded evidence."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from quack.graph.ids import deterministic_id
from tests.factories import EventLog


@dataclass
class Ids:
    domain: UUID = field(default_factory=uuid4)
    t_limits: UUID = field(default_factory=uuid4)
    t_deriv: UUID = field(default_factory=uuid4)
    t_dup: UUID = field(default_factory=uuid4)
    s_laws: UUID = field(default_factory=uuid4)
    s_cont: UUID = field(default_factory=uuid4)
    s_power: UUID = field(default_factory=uuid4)
    s_dup: UUID = field(default_factory=uuid4)
    m_sign: UUID = field(default_factory=uuid4)
    session: UUID = field(default_factory=uuid4)
    session2: UUID = field(default_factory=uuid4)
    diag_session: UUID = field(default_factory=uuid4)
    diag_task: UUID = field(default_factory=uuid4)
    task_a: UUID = field(default_factory=uuid4)
    task_b: UUID = field(default_factory=uuid4)
    task_c: UUID = field(default_factory=uuid4)
    note: UUID = field(default_factory=uuid4)
    sheet: UUID = field(default_factory=uuid4)
    digest: UUID = field(default_factory=uuid4)
    # Filled while building (deterministic ids of projector-created nodes).
    chain_rule_skill: str = ""
    product_rule_skill: str = ""
    extracted_misc: str = ""
    superseded_eval_seq: int = 0


def s(u: UUID) -> str:
    return str(u)


def _turn() -> str:
    return str(uuid4())


def build_scenario(log: EventLog | None = None, *, include_superseded_eval: bool = True):
    """Return (log, ids). With include_superseded_eval=False the history is the same except
    that the later-superseded evaluation (and the superseding event) never happened."""
    log = log or EventLog()
    ids = Ids()
    L = log.learner_id
    d = s(ids.domain)

    log.add("learner.registered", {"email": "l@q.x", "display_name": "L", "preferences": {}})
    log.add("learner.preferences_updated", {"preferences": {"pace": "fast"}})
    log.add("domain.created", {"domain_id": d, "name": "Calculus", "slug": "calc"})
    log.add(
        "structure.interview_completed",
        {"domain_id": d, "goals": "exam", "background": "school", "focus_areas": ["derivatives"]},
    )
    log.add(
        "structure.proposed",
        {
            "domain_id": d,
            "structure_version": 1,
            "topics": [
                {"topic_id": s(ids.t_limits), "name": "Limits"},
                {"topic_id": s(ids.t_deriv), "name": "Derivatives"},
                {"topic_id": s(ids.t_dup), "name": "Derivative rules"},
            ],
            "skills": [
                {"skill_id": s(ids.s_laws), "topic_id": s(ids.t_limits), "name": "Limit laws"},
                {"skill_id": s(ids.s_cont), "topic_id": s(ids.t_limits), "name": "Continuity"},
                {"skill_id": s(ids.s_power), "topic_id": s(ids.t_deriv), "name": "Power rule"},
                {"skill_id": s(ids.s_dup), "topic_id": s(ids.t_dup), "name": "Power rule (dup)"},
            ],
            "prereqs": [{"from_topic_id": s(ids.t_limits), "to_topic_id": s(ids.t_deriv)}],
            "skill_deps": [
                {"skill_id": s(ids.s_power), "depends_on_skill_id": s(ids.s_laws)},
                {"skill_id": s(ids.s_cont), "depends_on_skill_id": s(ids.s_laws)},
            ],
            "misconceptions": [
                {"misconception_id": s(ids.m_sign), "skill_id": s(ids.s_laws), "name": "Sign error"}
            ],
        },
        actor="structurer@1",
    )
    log.add("structure.confirmed", {"domain_id": d, "structure_version": 1}, actor="learner")

    # Diagnostics.
    ds = s(ids.diag_session)
    log.add(
        "task.generated",
        {
            "task_id": s(ids.diag_task),
            "domain_id": d,
            "topic_id": s(ids.t_limits),
            "family_id": "diag-limits",
            "difficulty": 0.3,
            "kind": "choice",
            "prompt_text": "pick",
            "reference_solution": {"option": "b"},
            "target_skills": [{"skill": {"skill_id": s(ids.s_laws)}, "weight": 1.0}],
        },
        actor="task_gen@1",
    )
    log.add(
        "diagnostic.started",
        {"domain_id": d, "session_id": ds, "candidate_topic_ids": [s(ids.t_limits)]},
    )
    log.add(
        "diagnostic.question_answered",
        {
            "domain_id": d,
            "session_id": ds,
            "turn_id": _turn(),
            "task_id": s(ids.diag_task),
            "topic_id": s(ids.t_limits),
            "skill_id": s(ids.s_laws),
            "answer": "b",
            "is_correct": True,
            "score": 1.0,
            "tested_depth": 0.6,
        },
        actor="learner",
    )
    log.add(
        "diagnostic.completed",
        {
            "domain_id": d,
            "session_id": ds,
            "tested_topic_ids": [s(ids.t_limits)],
            "inferred_priors": [
                {
                    "topic_id": s(ids.t_limits),
                    "skill_id": s(ids.s_cont),
                    "prior_mastery": 0.5,
                    "confidence": 0.4,
                    "source": "propagated",
                }
            ],
        },
        actor="diagnostic@1",
    )

    # Session 1: explanations with inline provisional skills, practice, extraction.
    sid = s(ids.session)
    log.advance(hours=1)
    log.add(
        "session.started",
        {
            "session_id": sid,
            "domain_id": d,
            "topic_id": s(ids.t_deriv),
            "mode": "learn",
            "goal": "derivatives",
            "plan": [{"step": "power rule"}],
        },
    )
    turn1 = _turn()
    log.add(
        "turn.student_replied",
        {"session_id": sid, "turn_id": turn1, "turn_index": 0, "text": "teach me"},
        actor="learner",
    )
    explained = log.add(
        "teacher.concept_explained",
        {
            "session_id": sid,
            "turn_id": turn1,
            "topic_id": s(ids.t_deriv),
            "skills": [
                {"skill_id": s(ids.s_power)},
                {"new_skill": {"name": "  Chain Rule ", "topic_id": s(ids.t_deriv)}},
            ],
            "depth": 0.8,
            "summary": "power and chain rules",
        },
        actor="teacher@1",
    )
    ids.chain_rule_skill = deterministic_id(L, explained.seq, "skill:1")
    log.add(
        "turn.teacher_replied",
        {
            "session_id": sid,
            "turn_id": turn1,
            "turn_index": 1,
            "text": "power rule: n x^(n-1)",
            "pedagogical_move": "explain",
            "focus_skill_ids": [s(ids.s_power)],
        },
        actor="teacher@1",
    )
    gen_a = log.add(
        "task.generated",
        {
            "task_id": s(ids.task_a),
            "domain_id": d,
            "topic_id": s(ids.t_deriv),
            "family_id": "power-a",
            "difficulty": 0.4,
            "kind": "math",
            "prompt_text": "d/dx x^3",
            "reference_solution": {"sympy": "3*x**2"},
            "target_skills": [
                {"skill": {"skill_id": s(ids.s_power)}, "weight": 1.0},
                {
                    "skill": {"new_skill": {"name": "chain rule", "topic_id": s(ids.t_deriv)}},
                    "weight": 0.5,
                },
            ],
            "target_misconceptions": ["sign error"],
        },
        actor="task_gen@1",
    )
    assert gen_a.seq > explained.seq
    turn2 = _turn()
    log.add(
        "task.assigned",
        {"session_id": sid, "turn_id": turn2, "task_id": s(ids.task_a), "topic_id": s(ids.t_deriv)},
        actor="teacher@1",
    )
    log.add(
        "task.hint_given",
        {
            "session_id": sid,
            "turn_id": turn2,
            "task_id": s(ids.task_a),
            "hint_level": 1,
            "hint_text": "bring the exponent down",
            "independence_penalty": 0.25,
        },
        actor="teacher@1",
    )
    log.add(
        "task.submitted",
        {
            "session_id": sid,
            "turn_id": turn2,
            "task_id": s(ids.task_a),
            "answer": "3x^2",
            "hints_used": 1,
        },
        actor="learner",
    )
    log.add(
        "task.evaluated",
        {
            "session_id": sid,
            "turn_id": turn2,
            "task_id": s(ids.task_a),
            "evaluator": "compute",
            "is_correct": True,
            "score": 1.0,
            "independence": 0.75,
            "feedback": "correct",
            "skill_scores": [
                {"skill_id": s(ids.s_power), "score": 1.0, "tested_depth": 0.5},
                {"skill_id": ids.chain_rule_skill, "score": 1.0, "tested_depth": 0.3},
            ],
        },
        actor="code",
    )
    extracted = log.add(
        "turn.extracted",
        {
            "session_id": sid,
            "turn_id": turn2,
            "observations": [
                {
                    "skill": {"skill_id": s(ids.s_power)},
                    "direction": "positive",
                    "strength": 0.7,
                    "independence": 0.75,
                    "tested_depth": 0.5,
                    "polarity_weight": 1.0,
                    "evidence_text": "3x^2",
                },
                {
                    "skill": {"new_skill": {"name": "Product rule", "topic_id": s(ids.t_dup)}},
                    "direction": "negative",
                    "strength": 0.5,
                    "independence": 1.0,
                    "tested_depth": 0.4,
                    "polarity_weight": 1.0,
                    "evidence_text": "uv' only",
                },
            ],
            "misconceptions": [
                {
                    "name": "Drops the minus",
                    "skill": {"skill_id": s(ids.s_laws)},
                    "topic_id": s(ids.t_limits),
                    "confidence": 0.6,
                    "evidence_text": "-x -> x",
                },
                {
                    "name": "sign ERROR",
                    "skill": {"skill_id": s(ids.s_laws)},
                    "topic_id": s(ids.t_limits),
                    "confidence": 0.8,
                    "evidence_text": "lost sign",
                },
            ],
        },
        actor="extractor@1",
    )
    ids.product_rule_skill = deterministic_id(L, extracted.seq, "skill:1")
    ids.extracted_misc = deterministic_id(L, extracted.seq, "misc:0")

    log.add(
        "task.generated",
        {
            "task_id": s(ids.task_b),
            "domain_id": d,
            "topic_id": s(ids.t_deriv),
            "family_id": "power-b",
            "difficulty": 0.5,
            "kind": "math",
            "prompt_text": "d/dx x^5",
            "reference_solution": {"sympy": "5*x**4"},
            "target_skills": [{"skill": {"skill_id": s(ids.s_power)}, "weight": 1.0}],
        },
        actor="task_gen@1",
    )
    turn3 = _turn()
    log.add(
        "task.assigned",
        {"session_id": sid, "turn_id": turn3, "task_id": s(ids.task_b), "topic_id": s(ids.t_deriv)},
        actor="teacher@1",
    )
    if include_superseded_eval:
        bad = log.add(
            "task.evaluated",
            {
                "session_id": sid,
                "turn_id": turn3,
                "task_id": s(ids.task_b),
                "evaluator": "llm",
                "is_correct": False,
                "score": 0.0,
                "independence": 1.0,
                "feedback": "wrong (misgraded)",
                "skill_scores": [{"skill_id": s(ids.s_power), "score": 0.0, "tested_depth": 0.5}],
                "triggered_misconceptions": [
                    {
                        "skill_id": s(ids.s_laws),
                        "name": "Sign error",
                        "misconception_id": s(ids.m_sign),
                    }
                ],
            },
            actor="evaluator@1",
        )
        ids.superseded_eval_seq = bad.seq
    log.add(
        "task.evaluated",
        {
            "session_id": sid,
            "turn_id": turn3,
            "task_id": s(ids.task_b),
            "evaluator": "hybrid",
            "is_correct": True,
            "score": 1.0,
            "independence": 1.0,
            "feedback": "correct",
            "skill_scores": [{"skill_id": s(ids.s_power), "score": 1.0, "tested_depth": 0.7}],
            "triggered_misconceptions": [
                {"skill_id": s(ids.s_laws), "name": "Sign error", "misconception_id": s(ids.m_sign)}
            ],
        },
        actor="evaluator@1",
    )
    if include_superseded_eval:
        log.add(
            "evidence.superseded",
            {"target_event_seq": ids.superseded_eval_seq, "reason": "re-evaluated"},
        )
    log.add(
        "session.checkpointed",
        {
            "session_id": sid,
            "up_to_turn_index": 3,
            "rolling_summary": "power rule practiced",
            "open_threads": [],
            "key_observations": ["needs hints"],
        },
        actor="checkpointer@1",
    )
    log.add(
        "session.digest_created",
        {
            "artifact_id": s(ids.digest),
            "session_id": sid,
            "domain_id": d,
            "topic_id": s(ids.t_deriv),
            "title": "Session digest",
            "text": "We practiced the power rule.",
            "covered_skill_ids": [s(ids.s_power)],
        },
        actor="artifacts@1",
    )
    log.add("session.completed", {"session_id": sid, "domain_id": d, "summary_stats": {"turns": 4}})

    # Curation: promote, merge skills/topics/misconceptions.
    log.advance(hours=2)
    log.add(
        "skill.promoted",
        {
            "domain_id": d,
            "skill_id": ids.chain_rule_skill,
            "topic_id": s(ids.t_deriv),
            "name": "Chain rule",
            "description": "composite functions",
        },
        actor="curator@1",
    )
    log.add(
        "skill.merged",
        {
            "domain_id": d,
            "source_skill_id": s(ids.s_dup),
            "target_skill_id": s(ids.s_power),
            "reason": "duplicate",
        },
        actor="curator@1",
    )
    log.add(
        "topic.merged",
        {
            "domain_id": d,
            "source_topic_id": s(ids.t_dup),
            "target_topic_id": s(ids.t_deriv),
            "reason": "duplicate",
        },
        actor="curator@1",
    )
    log.add(
        "misconception.merged",
        {
            "domain_id": d,
            "source_misconception_id": ids.extracted_misc,
            "target_misconception_id": s(ids.m_sign),
            "reason": "same idea",
        },
        actor="curator@1",
    )
    log.add(
        "structure.edited",
        {
            "domain_id": d,
            "structure_version": 2,
            "added_topics": [],
            "updated_topics": [{"topic_id": s(ids.t_limits), "name": "Limits & continuity"}],
            "added_prereqs": [],
            "removed_prereqs": [],
            "added_skills": [
                {"skill_id": s(uuid4()), "topic_id": s(ids.t_limits), "name": "Squeeze theorem"}
            ],
            "updated_skills": [{"skill_id": s(ids.s_cont), "description": "epsilon-delta"}],
        },
        actor="learner",
    )

    # Session 2: references to merged ids (old source ids) must resolve through Alias.
    sid2 = s(ids.session2)
    log.advance(days=2)
    log.add(
        "session.started",
        {
            "session_id": sid2,
            "domain_id": d,
            "topic_id": s(ids.t_dup),
            "mode": "practice",
            "goal": None,
            "plan": [],
        },
    )
    log.add(
        "task.generated",
        {
            "task_id": s(ids.task_c),
            "domain_id": d,
            "topic_id": s(ids.t_dup),
            "family_id": "power-c",
            "difficulty": 0.6,
            "kind": "open",
            "prompt_text": "explain",
            "reference_solution": {"text": "..."},
            "target_skills": [{"skill": {"skill_id": s(ids.s_dup)}, "weight": 1.0}],
        },
        actor="task_gen@1",
    )
    turn4 = _turn()
    log.add(
        "task.assigned",
        {"session_id": sid2, "turn_id": turn4, "task_id": s(ids.task_c), "topic_id": s(ids.t_dup)},
        actor="teacher@1",
    )
    log.add(
        "task.submitted",
        {
            "session_id": sid2,
            "turn_id": turn4,
            "task_id": s(ids.task_c),
            "answer": "n x^(n-1)",
            "hints_used": 0,
        },
        actor="learner",
    )
    log.add(
        "task.evaluated",
        {
            "session_id": sid2,
            "turn_id": turn4,
            "task_id": s(ids.task_c),
            "evaluator": "compute",
            "is_correct": True,
            "score": 1.0,
            "independence": 1.0,
            "feedback": "great",
            "skill_scores": [{"skill_id": s(ids.s_dup), "score": 1.0, "tested_depth": 0.8}],
            "triggered_misconceptions": [
                {"skill_id": s(ids.s_laws), "name": "x", "misconception_id": ids.extracted_misc}
            ],
        },
        actor="code",
    )
    log.add(
        "turn.extracted",
        {
            "session_id": sid2,
            "turn_id": turn4,
            "observations": [
                {
                    "skill": {"skill_id": ids.product_rule_skill},
                    "direction": "positive",
                    "strength": 0.8,
                    "independence": 1.0,
                    "tested_depth": 0.6,
                    "polarity_weight": 1.0,
                    "evidence_text": "uv' + u'v",
                }
            ],
        },
        actor="extractor@1",
    )
    log.add(
        "misconception.resolved",
        {"domain_id": d, "misconception_id": s(ids.m_sign), "resolution_reason": "teacher check"},
        actor="learner",
    )
    log.add(
        "artifact.note_created",
        {
            "artifact_id": s(ids.note),
            "domain_id": d,
            "topic_id": s(ids.t_dup),
            "session_id": sid2,
            "title": "My note",
            "text": "power rule",
        },
        actor="learner",
    )
    log.add(
        "artifact.note_updated",
        {"artifact_id": s(ids.note), "version": 2, "title": "My note", "text": "power rule!"},
        actor="learner",
    )
    log.add(
        "artifact.cheatsheet_updated",
        {
            "artifact_id": s(ids.sheet),
            "domain_id": d,
            "topic_id": s(ids.t_deriv),
            "version": 1,
            "title": "Derivatives",
            "text": "d/dx x^n = n x^(n-1)",
        },
        actor="artifacts@1",
    )
    log.add("artifact.note_deleted", {"artifact_id": s(ids.note)}, actor="learner")
    log.add(
        "session.abandoned",
        {"session_id": sid2, "domain_id": d, "reason": "inactivity_timeout"},
    )
    log.add(
        "topic.status_changed",
        {"domain_id": d, "topic_id": s(ids.t_limits), "status": "active", "reason": None},
        actor="learner",
    )
    log.add("domain.archived", {"domain_id": d, "reason": "done"}, actor="learner")
    return log, ids
