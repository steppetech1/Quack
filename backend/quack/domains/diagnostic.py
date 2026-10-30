"""Adaptive diagnostics over the domain DAG and propagation of `inferred_priors`
upward and downward along prerequisite edges (ТЗ §2.3, §3, §4; Т-§3.3).
"""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import orjson

from quack.api.app import AppResources
from quack.api.errors import ApiError
from quack.config import Settings
from quack.domains.schemas import (
    AnswerDiagnosticRequest,
    AnswerDiagnosticResponse,
    DiagnosticOutput,
    DiagnosticQuestion,
    FinishDiagnosticRequest,
    FinishDiagnosticResponse,
    PrereqEdge,
    SkillDepEdge,
    StartDiagnosticRequest,
    StartDiagnosticResponse,
    TopicPriorItem,
)
from quack.events.append import append_events
from quack.events.models import NewEvent
from quack.events.reader import read_learner_events
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.slice import SliceDeps, read_consistent_slice
from quack.llm.gateway import LLMGateway

DIAGNOSTIC_MAX_QUESTIONS = 5
PRIOR_TESTED_FACTOR = 0.5
PRIOR_TESTED_CONFIDENCE = 0.5
PRIOR_PROPAGATED_SUCCESS_THRESHOLD = 0.7
PRIOR_PROPAGATED_FAIL_THRESHOLD = 0.5
PRIOR_PROPAGATED_DECAY = 0.85
PRIOR_DOWN_BASE_MASTERY = 0.4
PRIOR_DOWN_BASE_CONFIDENCE = 0.35
PRIOR_UP_FAIL_MASTERY = 0.05
PRIOR_UP_BASE_CONFIDENCE = 0.4


def compute_inferred_priors(
    skills: Sequence[tuple[UUID, UUID]],
    skill_deps: Sequence[SkillDepEdge],
    topic_prereqs: Sequence[PrereqEdge],
    tested_scores: dict[UUID, float],
) -> list[TopicPriorItem]:
    """Compute `TopicPriorItem` list for domain skills from tested scores, propagating
    downward to prerequisites on success (`score >= 0.7`) and upward to dependents on failure
    (`score < 0.5`) (ТЗ §2.3, Т-§3.3).
    """
    skill_scores: dict[UUID, float] = {}
    topic_to_skills: dict[UUID, list[UUID]] = {}
    skill_to_topic: dict[UUID, UUID] = {}

    for s_id, t_id in skills:
        skill_to_topic[s_id] = t_id
        topic_to_skills.setdefault(t_id, []).append(s_id)
        if s_id in tested_scores:
            skill_scores[s_id] = tested_scores[s_id]
        elif t_id in tested_scores:
            skill_scores[s_id] = tested_scores[t_id]

    prereqs_of: dict[UUID, set[UUID]] = {s_id: set() for s_id, _ in skills}
    dependents_of: dict[UUID, set[UUID]] = {s_id: set() for s_id, _ in skills}

    for dep_edge in skill_deps:
        s_dep = dep_edge.skill_id
        s_pre = dep_edge.depends_on_skill_id
        if s_dep in prereqs_of and s_pre in prereqs_of:
            prereqs_of[s_dep].add(s_pre)
            dependents_of[s_pre].add(s_dep)

    for p_edge in topic_prereqs:
        from_skills = topic_to_skills.get(p_edge.from_topic_id, [])
        to_skills = topic_to_skills.get(p_edge.to_topic_id, [])
        for s_pre in from_skills:
            for s_dep in to_skills:
                prereqs_of[s_dep].add(s_pre)
                dependents_of[s_pre].add(s_dep)

    min_down_dist: dict[UUID, int] = {}
    for s_test, score in skill_scores.items():
        if score >= PRIOR_PROPAGATED_SUCCESS_THRESHOLD:
            queue = [(s_pre, 1) for s_pre in prereqs_of.get(s_test, set())]
            visited = {s_test}
            while queue:
                curr, dist = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                if curr not in skill_scores and (
                    curr not in min_down_dist or dist < min_down_dist[curr]
                ):
                    min_down_dist[curr] = dist
                for nxt in prereqs_of.get(curr, set()):
                    if nxt not in visited:
                        queue.append((nxt, dist + 1))

    min_up_dist: dict[UUID, int] = {}
    for s_test, score in skill_scores.items():
        if score < PRIOR_PROPAGATED_FAIL_THRESHOLD:
            queue = [(s_dep, 1) for s_dep in dependents_of.get(s_test, set())]
            visited = {s_test}
            while queue:
                curr, dist = queue.pop(0)
                if curr in visited:
                    continue
                visited.add(curr)
                if curr not in skill_scores and (
                    curr not in min_up_dist or dist < min_up_dist[curr]
                ):
                    min_up_dist[curr] = dist
                for nxt in dependents_of.get(curr, set()):
                    if nxt not in visited:
                        queue.append((nxt, dist + 1))

    priors: list[TopicPriorItem] = []
    for s_id, t_id in skills:
        if s_id in skill_scores:
            score = skill_scores[s_id]
            prior_m = min(0.5, score * PRIOR_TESTED_FACTOR)
            conf = PRIOR_TESTED_CONFIDENCE
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=prior_m,
                    confidence=conf,
                    source="tested",
                )
            )
        elif s_id in min_up_dist:
            dist = min_up_dist[s_id]
            prior_m = PRIOR_UP_FAIL_MASTERY
            conf = PRIOR_UP_BASE_CONFIDENCE * (PRIOR_PROPAGATED_DECAY**dist)
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=prior_m,
                    confidence=conf,
                    source="propagated",
                )
            )
        elif s_id in min_down_dist:
            dist = min_down_dist[s_id]
            prior_m = PRIOR_DOWN_BASE_MASTERY * (PRIOR_PROPAGATED_DECAY**dist)
            conf = PRIOR_DOWN_BASE_CONFIDENCE * (PRIOR_PROPAGATED_DECAY**dist)
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=prior_m,
                    confidence=conf,
                    source="propagated",
                )
            )
        else:
            priors.append(
                TopicPriorItem(
                    topic_id=t_id,
                    skill_id=s_id,
                    prior_mastery=0.0,
                    confidence=0.0,
                    source="propagated",
                )
            )
    return priors


async def _load_domain_dag(
    resources: AppResources, learner_id: UUID, domain_id: UUID
) -> tuple[
    list[tuple[UUID, str, float]],
    list[tuple[UUID, UUID, str]],
    list[PrereqEdge],
    list[SkillDepEdge],
]:
    """Query active topics, skills, prereq edges, and skill deps from Neo4j via
    `read_consistent_slice` (ТЗ §2.3, Т-§3.3).
    """
    deps = SliceDeps(
        session_factory=resources.session_factory,
        redis=resources.redis,
        applied_seq_reader=lambda lid: read_applied_seq(resources.neo4j, str(lid)),
    )

    async def _loader() -> tuple[
        list[tuple[UUID, str, float]],
        list[tuple[UUID, UUID, str]],
        list[PrereqEdge],
        list[SkillDepEdge],
    ]:
        async with resources.neo4j.session() as s:
            t_res = await s.run(
                "MATCH (d:Domain {id: $d, learnerId: $l})-[:HAS_TOPIC]->(t:Topic) "
                "WHERE t.status = 'active' "
                "RETURN t.id AS id, t.name AS name, properties(t) AS props",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            topics: list[tuple[UUID, str, float]] = []
            async for rec in t_res:
                tid = UUID(rec["id"])
                name = str(rec["name"] or "")
                props = rec["props"] or {}
                hub = float(props.get("hubScore", 0.0))
                topics.append((tid, name, hub))

            sk_res = await s.run(
                "MATCH (t:Topic)<-[:PART_OF]-(sk:Skill) "
                "WHERE t.domainId = $d AND t.learnerId = $l AND sk.status = 'active' "
                "RETURN sk.id AS id, t.id AS topic_id, sk.name AS name",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            skills: list[tuple[UUID, UUID, str]] = []
            async for rec in sk_res:
                skills.append((UUID(rec["id"]), UUID(rec["topic_id"]), str(rec["name"] or "")))

            pr_res = await s.run(
                "MATCH (a:Topic)-[:PREREQ_OF]->(b:Topic) "
                "WHERE a.domainId = $d AND b.domainId = $d AND a.learnerId = $l "
                "AND a.status = 'active' AND b.status = 'active' "
                "RETURN a.id AS from_id, b.id AS to_id",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            topic_prereqs = [
                PrereqEdge(from_topic_id=UUID(rec["from_id"]), to_topic_id=UUID(rec["to_id"]))
                async for rec in pr_res
            ]

            dep_res = await s.run(
                "MATCH (a:Skill)-[:DEPENDS_ON]->(b:Skill) "
                "WHERE a.domainId = $d AND b.domainId = $d AND a.learnerId = $l "
                "AND a.status = 'active' AND b.status = 'active' "
                "RETURN a.id AS sid, b.id AS did",
                {"d": str(domain_id), "l": str(learner_id)},
            )
            skill_deps = [
                SkillDepEdge(skill_id=UUID(rec["sid"]), depends_on_skill_id=UUID(rec["did"]))
                async for rec in dep_res
            ]

        return topics, skills, topic_prereqs, skill_deps

    slice_res = await read_consistent_slice(learner_id, min_seq=None, loader_fn=_loader, deps=deps)
    return slice_res.data


def _topological_candidate_topics(
    topics: list[tuple[UUID, str, float]],
    topic_prereqs: list[PrereqEdge],
) -> list[UUID]:
    """Sort active topics topologically by PREREQ_OF, then order starting from DAG center/hub."""
    topic_ids = [t[0] for t in topics]
    hub_map = {t[0]: t[2] for t in topics}

    adj: dict[UUID, list[UUID]] = {tid: [] for tid in topic_ids}
    in_degree: dict[UUID, int] = {tid: 0 for tid in topic_ids}
    for edge in topic_prereqs:
        if edge.from_topic_id in adj and edge.to_topic_id in in_degree:
            adj[edge.from_topic_id].append(edge.to_topic_id)
            in_degree[edge.to_topic_id] += 1

    queue = [tid for tid in topic_ids if in_degree[tid] == 0]
    topo_order: list[UUID] = []
    while queue:
        u = queue.pop(0)
        topo_order.append(u)
        for v in adj.get(u, []):
            in_degree[v] -= 1
            if in_degree[v] == 0:
                queue.append(v)

    for tid in topic_ids:
        if tid not in topo_order:
            topo_order.append(tid)

    if not topo_order:
        return []

    mid_idx = len(topo_order) // 2
    candidates = sorted(
        topo_order,
        key=lambda tid: (abs(topo_order.index(tid) - mid_idx), -hub_map.get(tid, 0.0)),
    )
    return candidates


async def _save_session_state(redis: Any, session_id: UUID, state: dict[str, Any]) -> None:
    if redis is not None:
        with contextlib.suppress(Exception):
            await redis.set(
                f"diagnostic:session:{session_id}",
                orjson.dumps(state).decode(),
                ex=86400,
            )


async def _load_session_state(
    resources: AppResources, learner_id: UUID, session_id: UUID
) -> dict[str, Any] | None:
    if resources.redis is not None:
        with contextlib.suppress(Exception):
            data = await resources.redis.get(f"diagnostic:session:{session_id}")
            if data:
                return orjson.loads(data)

    async with resources.session_factory() as session:
        events = await read_learner_events(session, learner_id)

    diag_events = [e for e in events if e.session_id == session_id]
    if not diag_events:
        return None

    started = next((e for e in diag_events if e.type == "diagnostic.started"), None)
    if not started:
        return None

    candidate_ids = started.payload.get("candidate_topic_ids", [])
    domain_id = started.domain_id

    answered = [e for e in diag_events if e.type == "diagnostic.question_answered"]
    tested_topics = [UUID(e.payload["topic_id"]) for e in answered]
    tested_scores = {e.payload["skill_id"]: float(e.payload["score"]) for e in answered}

    generated = [e for e in diag_events if e.type == "task.generated"]
    current_q: dict[str, Any] = {}
    if generated:
        last_gen = generated[-1].payload
        current_q = {
            "task_id": last_gen["task_id"],
            "topic_id": last_gen["topic_id"],
            "skill_id": last_gen["target_skills"][0]["skill"]["skill_id"]
            if last_gen.get("target_skills")
            else "",
            "correct_answer": last_gen.get("reference_solution", {}).get("answer", "A"),
            "prompt_text": last_gen.get("prompt_text", ""),
            "options": last_gen.get("options", []),
        }

    return {
        "domain_id": str(domain_id),
        "session_id": str(session_id),
        "candidate_topic_ids": candidate_ids,
        "tested_topic_ids": [str(t) for t in tested_topics],
        "tested_scores": tested_scores,
        "current_question": current_q,
        "step_count": len(answered),
    }


async def start_diagnostic(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: StartDiagnosticRequest,
) -> StartDiagnosticResponse:
    """Select mid-DAG high-hub candidate topics, generate first diagnostic question
    (`task.generated`), and emit `diagnostic.started` (ТЗ §2.3, §3; Т-§3.3).
    """
    topics, skills, topic_prereqs, _ = await _load_domain_dag(resources, learner_id, domain_id)
    if not topics:
        raise ApiError("NOT_FOUND", "No active topics found in domain", status_code=404)

    candidate_topic_ids = _topological_candidate_topics(topics, topic_prereqs)
    first_topic_id = candidate_topic_ids[0]

    skills_for_topic = [s for s in skills if s[1] == first_topic_id]
    first_skill_id, _, first_skill_name = (
        skills_for_topic[0] if skills_for_topic else (uuid4(), first_topic_id, "Core Skill")
    )
    first_topic_name = next((t[1] for t in topics if t[0] == first_topic_id), "Core Topic")

    session_id = uuid4()
    task_id = uuid4()

    try:
        res = await llm.generate_structured(
            role="diagnostic",
            prompt_version=1,
            response_model=DiagnosticOutput,
            variables={
                "mode": "generate",
                "topic_name": first_topic_name,
                "skill_name": first_skill_name,
                "question_prompt": "",
                "student_answer": "",
            },
            learner_id=learner_id,
            session_id=session_id,
            turn_id=None,
            idempotency_key=f"diag-gen:{task_id}",
        )
        diag_out = res.value
    except Exception:
        diag_out = DiagnosticOutput(
            prompt_text=f"What is the principle behind {first_skill_name}?",
            options=["A", "B", "C"],
            correct_answer="A",
            is_correct=False,
            score=0.0,
            tested_depth=0.5,
        )

    options = diag_out.options if diag_out.options else ["A", "B", "C"]
    correct_ans = diag_out.correct_answer or options[0]

    task_event = NewEvent(
        type="task.generated",
        actor="code",
        domain_id=domain_id,
        idempotency_key=f"tgen:{task_id}",
        payload={
            "task_id": str(task_id),
            "domain_id": str(domain_id),
            "topic_id": str(first_topic_id),
            "family_id": f"diag_{first_skill_id.hex[:8]}",
            "difficulty": 0.5,
            "kind": "choice",
            "prompt_text": diag_out.prompt_text or "Diagnostic check",
            "reference_solution": {"answer": correct_ans},
            "target_skills": [{"skill": {"skill_id": str(first_skill_id)}, "weight": 1.0}],
            "target_misconceptions": [],
        },
    )

    started_event = NewEvent(
        type="diagnostic.started",
        actor="learner",
        domain_id=domain_id,
        session_id=session_id,
        idempotency_key=req.idempotency_key,
        payload={
            "domain_id": str(domain_id),
            "session_id": str(session_id),
            "candidate_topic_ids": [str(tid) for tid in candidate_topic_ids],
        },
    )

    async with resources.session_factory() as session, session.begin():
        await append_events(session, learner_id, [task_event, started_event], publish_wakeup=False)

    state = {
        "domain_id": str(domain_id),
        "session_id": str(session_id),
        "candidate_topic_ids": [str(tid) for tid in candidate_topic_ids],
        "tested_topic_ids": [],
        "tested_scores": {},
        "current_question": {
            "task_id": str(task_id),
            "topic_id": str(first_topic_id),
            "skill_id": str(first_skill_id),
            "correct_answer": correct_ans,
            "prompt_text": diag_out.prompt_text,
            "options": options,
        },
        "step_count": 0,
    }
    await _save_session_state(resources.redis, session_id, state)

    first_question = DiagnosticQuestion(
        task_id=task_id,
        topic_id=first_topic_id,
        skill_id=first_skill_id,
        kind="choice",
        prompt_text=diag_out.prompt_text,
        options=options,
    )
    return StartDiagnosticResponse(
        session_id=session_id,
        candidate_topic_ids=candidate_topic_ids,
        next_question=first_question,
    )


async def answer_diagnostic(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: AnswerDiagnosticRequest,
) -> AnswerDiagnosticResponse:
    """Evaluate diagnostic answer, emit `diagnostic.question_answered`, and adaptively select
    the next question upward (if correct) or downward (if incorrect) along the DAG up to
    `DIAGNOSTIC_MAX_QUESTIONS` (ТЗ §2.3, §3; Т-§3.3).
    """
    state = await _load_session_state(resources, learner_id, req.session_id)
    if not state:
        raise ApiError("NOT_FOUND", "Diagnostic session not found", status_code=404)

    curr_q = state.get("current_question", {})
    correct_ans = curr_q.get("correct_answer", "A")
    curr_topic_id = UUID(curr_q["topic_id"])
    curr_skill_id = UUID(curr_q["skill_id"])

    is_correct = req.answer.strip().lower() == correct_ans.strip().lower()
    score = 1.0 if is_correct else 0.0

    turn_id = uuid4()
    answered_event = NewEvent(
        type="diagnostic.question_answered",
        actor="learner",
        domain_id=domain_id,
        session_id=req.session_id,
        turn_id=turn_id,
        idempotency_key=req.idempotency_key,
        payload={
            "domain_id": str(domain_id),
            "session_id": str(req.session_id),
            "turn_id": str(turn_id),
            "task_id": str(req.task_id),
            "topic_id": str(curr_topic_id),
            "skill_id": str(curr_skill_id),
            "answer": req.answer,
            "is_correct": is_correct,
            "score": score,
            "confidence_self_report": req.confidence_self_report,
            "response_time_sec": req.response_time_sec,
            "tested_depth": 0.5,
        },
    )

    tested_topic_ids = [UUID(tid) for tid in state.get("tested_topic_ids", [])]
    tested_topic_ids.append(curr_topic_id)
    tested_scores = state.get("tested_scores", {})
    tested_scores[str(curr_skill_id)] = score
    step_count = state.get("step_count", 0) + 1

    topics, skills, topic_prereqs, _ = await _load_domain_dag(resources, learner_id, domain_id)
    tested_set = set(tested_topic_ids)

    adj_dependents: dict[UUID, list[UUID]] = {}
    adj_prereqs: dict[UUID, list[UUID]] = {}
    for p in topic_prereqs:
        adj_dependents.setdefault(p.from_topic_id, []).append(p.to_topic_id)
        adj_prereqs.setdefault(p.to_topic_id, []).append(p.from_topic_id)

    next_topic: UUID | None = None
    if step_count < DIAGNOSTIC_MAX_QUESTIONS:
        if is_correct:
            queue = list(adj_dependents.get(curr_topic_id, []))
            visited = set(queue)
            while queue:
                cand = queue.pop(0)
                if cand not in tested_set:
                    next_topic = cand
                    break
                for nxt in adj_dependents.get(cand, []):
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)
        else:
            queue = list(adj_prereqs.get(curr_topic_id, []))
            visited = set(queue)
            while queue:
                cand = queue.pop(0)
                if cand not in tested_set:
                    next_topic = cand
                    break
                for nxt in adj_prereqs.get(cand, []):
                    if nxt not in visited:
                        visited.add(nxt)
                        queue.append(nxt)

        if next_topic is None:
            cand_order = [UUID(t) for t in state.get("candidate_topic_ids", [])]
            for cand in cand_order:
                if cand not in tested_set:
                    next_topic = cand
                    break

    events_to_append = [answered_event]
    next_question: DiagnosticQuestion | None = None
    should_finish = next_topic is None or step_count >= DIAGNOSTIC_MAX_QUESTIONS

    if next_topic is not None and not should_finish:
        sk_cand = [s for s in skills if s[1] == next_topic]
        next_sk_id, _, next_sk_name = (
            sk_cand[0] if sk_cand else (uuid4(), next_topic, "Diagnostic Skill")
        )
        next_t_name = next((t[1] for t in topics if t[0] == next_topic), "Topic")

        next_task_id = uuid4()
        try:
            res = await llm.generate_structured(
                role="diagnostic",
                prompt_version=1,
                response_model=DiagnosticOutput,
                variables={
                    "mode": "generate",
                    "topic_name": next_t_name,
                    "skill_name": next_sk_name,
                    "question_prompt": "",
                    "student_answer": "",
                },
                learner_id=learner_id,
                session_id=req.session_id,
                turn_id=turn_id,
                idempotency_key=f"diag-gen:{next_task_id}",
            )
            next_diag_out = res.value
        except Exception:
            next_diag_out = DiagnosticOutput(
                prompt_text=f"Diagnostic question on {next_sk_name}?",
                options=["A", "B", "C"],
                correct_answer="A",
                is_correct=False,
                score=0.0,
                tested_depth=0.5,
            )

        next_opts = next_diag_out.options if next_diag_out.options else ["A", "B", "C"]
        next_correct = next_diag_out.correct_answer or next_opts[0]

        next_task_event = NewEvent(
            type="task.generated",
            actor="code",
            domain_id=domain_id,
            idempotency_key=f"tgen:{next_task_id}",
            payload={
                "task_id": str(next_task_id),
                "domain_id": str(domain_id),
                "topic_id": str(next_topic),
                "family_id": f"diag_{next_sk_id.hex[:8]}",
                "difficulty": 0.5,
                "kind": "choice",
                "prompt_text": next_diag_out.prompt_text or "Diagnostic question",
                "reference_solution": {"answer": next_correct},
                "target_skills": [{"skill": {"skill_id": str(next_sk_id)}, "weight": 1.0}],
                "target_misconceptions": [],
            },
        )
        events_to_append.append(next_task_event)

        state["current_question"] = {
            "task_id": str(next_task_id),
            "topic_id": str(next_topic),
            "skill_id": str(next_sk_id),
            "correct_answer": next_correct,
            "prompt_text": next_diag_out.prompt_text,
            "options": next_opts,
        }
        next_question = DiagnosticQuestion(
            task_id=next_task_id,
            topic_id=next_topic,
            skill_id=next_sk_id,
            kind="choice",
            prompt_text=next_diag_out.prompt_text,
            options=next_opts,
        )

    state["tested_topic_ids"] = [str(t) for t in tested_topic_ids]
    state["tested_scores"] = tested_scores
    state["step_count"] = step_count

    async with resources.session_factory() as session, session.begin():
        await append_events(session, learner_id, events_to_append, publish_wakeup=False)

    await _save_session_state(resources.redis, req.session_id, state)

    return AnswerDiagnosticResponse(
        is_correct=is_correct,
        score=score,
        next_question=next_question,
        should_finish=should_finish,
    )


async def finish_diagnostic(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    req: FinishDiagnosticRequest,
) -> FinishDiagnosticResponse:
    """Compute `inferred_priors` across the DAG and emit `diagnostic.completed`
    (ТЗ §2.3, §3; Т-§3.3).
    """
    state = await _load_session_state(resources, learner_id, req.session_id)
    tested_topic_ids = [UUID(tid) for tid in state.get("tested_topic_ids", [])] if state else []
    tested_scores = (
        {UUID(sid): score for sid, score in state.get("tested_scores", {}).items()} if state else {}
    )

    _, skills, topic_prereqs, skill_deps = await _load_domain_dag(resources, learner_id, domain_id)
    skill_tuples = [(s[0], s[1]) for s in skills]

    inferred_priors = compute_inferred_priors(
        skills=skill_tuples,
        skill_deps=skill_deps,
        topic_prereqs=topic_prereqs,
        tested_scores=tested_scores,
    )

    completed_event = NewEvent(
        type="diagnostic.completed",
        actor="learner",
        domain_id=domain_id,
        session_id=req.session_id,
        idempotency_key=req.idempotency_key,
        payload={
            "domain_id": str(domain_id),
            "session_id": str(req.session_id),
            "tested_topic_ids": [str(tid) for tid in tested_topic_ids],
            "inferred_priors": [item.model_dump(mode="json") for item in inferred_priors],
        },
    )

    async with resources.session_factory() as session, session.begin():
        stored = await append_events(session, learner_id, [completed_event], publish_wakeup=False)

    seq = stored[0].seq if stored else 0
    return FinishDiagnosticResponse(
        tested_topic_ids=tested_topic_ids,
        inferred_priors=inferred_priors,
        seq=seq,
    )
