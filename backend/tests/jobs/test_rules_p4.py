"""ТЗ Ф4 WP7.1, seam S6, T-D1 (G1 part): every event-only row of the table puts its own kind,
class, `lock_key`, `coalesce_key`, `required_seq`; kinds without a rule are forbidden."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from quack.config import Settings
from quack.events.log import StoredEvent
from quack.jobs import rules_p4 as r
from tests.events.samples import sample_payload, uid

SETTINGS = Settings()
LEARNER = UUID(int=7)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def _event(event_type: str, seq: int = 10, **changes: Any) -> StoredEvent:
    payload = {**sample_payload(event_type), **changes.pop("payload", {})}
    return StoredEvent(
        learner_id=LEARNER,
        seq=seq,
        id=seq,
        type=event_type,
        schema_version=1,
        actor="api",
        domain_id=None,
        session_id=changes.pop("session_id", UUID(int=99)),
        causation_seq=None,
        correlation_id=None,
        idempotency_key=f"k{seq}",
        payload_hash="h",
        occurred_at=NOW,
        payload=payload,
        source_refs=[],
    )


def _kinds(event: StoredEvent) -> list[str]:
    return [p.kind for p in r.plan_jobs(event, SETTINGS)]


def _one(event: StoredEvent) -> r.JobPlan:
    (plan,) = r.plan_jobs(event, SETTINGS)
    return plan


def _answer(kind: str, **extra: Any) -> StoredEvent:
    answer = {"kind": kind, "value": "x", **extra}
    return _event("answer.submitted", payload={"answer": answer})


def test_every_kind_of_the_spec_is_listed_once_and_removed_ones_are_gone() -> None:
    # 24 kinds of the list of ТЗ §6.5 + `agent.topic_split` (WP8.2, D-A-226) + 5 periodic
    assert len(set(r.JOB_KINDS + r.PERIODIC_JOB_KINDS)) == 25 + 5
    assert not set(r.REMOVED_JOB_KINDS) & set(r.JOB_KINDS + r.PERIODIC_JOB_KINDS)


def test_rule_table() -> None:
    known = set(r.KIND_SPEC)
    for row in r.RULES:
        assert set(row.job_kinds) <= known, row


def test_every_non_periodic_kind_has_a_row() -> None:
    covered = {k for row in r.RULES for k in row.job_kinds}
    assert covered >= set(r.JOB_KINDS) - {"export.data"}  # export.data — by request (WP25)


def test_rows_with_events_exist_in_the_catalog() -> None:
    from quack.events.registry import payload_model

    for row in r.RULES:
        if row.event_type != "*":
            payload_model(row.event_type)


def test_fast_answer_puts_check_symbolic_after_the_delay() -> None:
    plan = _one(_answer("expression", sympy="x+1", latex="x+1"))
    assert plan.kind == "check.symbolic"
    assert plan.priority_class == "P1" and plan.visible
    assert plan.run_at == NOW + timedelta(milliseconds=SETTINGS.symbolic_check_delay_ms)
    assert plan.required_seq is None  # reads only the log
    assert plan.payload["answer_seq"] == 10 and plan.payload["part"] == "main"
    assert _one(_answer("quantity")).kind == "check.symbolic"


def test_number_as_expression_is_fast_plain_number_is_instant() -> None:
    assert _kinds(_answer("number", sympy="2*sqrt(3)")) == ["check.symbolic"]
    assert _kinds(_answer("number")) == []
    assert _kinds(_answer("choice")) == []


@pytest.mark.parametrize("kind", ["proof", "free_text", "drawing"])
def test_slow_answer_puts_check_model(kind: str) -> None:
    plan = _one(_answer(kind))
    assert plan.kind == "check.model" and plan.priority_class == "P1"
    assert plan.job_key.endswith(":10:main")


def test_check_failed_goes_to_check_model_with_the_key_of_its_answer() -> None:
    failed = _event("check.failed", seq=12, payload={"answer_seq": 10, "part": "main"})
    assert _one(failed).job_key == _one(_answer("proof")).job_key  # never doubled


def test_episode_closed_only_with_content() -> None:
    plan = _one(_event("episode.closed"))
    assert plan.kind == "extract.episode" and plan.required_seq == 10
    assert plan.payload["from_seq"] == 1 and plan.payload["to_seq"] == 9
    assert _kinds(_event("episode.closed", payload={"has_content": False})) == []


def test_task_closed_and_session_ended_put_the_analyst_with_one_lock() -> None:
    run = _one(_event("task.closed"))
    assert run.kind == "analyst.run"
    assert run.lock_key == run.coalesce_key == f"analyst:{LEARNER}"
    assert run.required_seq == 10
    run2, deep = r.plan_jobs(_event("session.ended"), SETTINGS)
    assert (run2.kind, deep.kind) == ("analyst.run", "analyst.deep")
    assert deep.lock_key == run2.lock_key
    assert deep.coalesce_key != run2.coalesce_key  # coalescing must not merge the two kinds


def test_digest_with_divergence_puts_analyst_deep() -> None:
    assert _kinds(_event("digest.written", payload={"plan_divergence": True})) == ["analyst.deep"]
    assert _kinds(_event("digest.written", payload={"plan_divergence": False})) == []


def test_verdict_with_misconception_id_puts_analyst_and_describe() -> None:
    event = _event("verdict.recorded", payload={"misconception_id": uid("m")})
    run, describe = r.plan_jobs(event, SETTINGS)
    assert (run.kind, describe.kind) == ("analyst.run", "misconception.describe")
    assert describe.lock_key == f"misconception:{uid('m')}"
    assert describe.priority_class == "P3" and not describe.visible


def test_hypothesis_without_id_gets_the_analyst_only() -> None:
    event = _event("verdict.recorded", payload={"misconception_hypothesis": "drops the minus"})
    assert _kinds(event) == ["analyst.run"]
    assert _kinds(_event("verdict.recorded")) == []


def test_uncertain_diagnosis_asks_for_a_second_opinion() -> None:
    base = sample_payload("observation.diagnosed")["diagnosis"]
    question = {**base, "kind": "question"}
    assert "second_opinion.diagnosis" in _kinds(
        _event("observation.diagnosed", payload={"diagnosis": question})
    )
    error = {**base, "kind": "error"}
    assert "second_opinion.diagnosis" not in _kinds(
        _event("observation.diagnosed", payload={"diagnosis": error})
    )


def test_turn_with_tasks_puts_task_check() -> None:
    plan = _one(_event("turn.committed"))
    assert plan.kind == "task.check" and plan.priority_class == "P1"
    assert plan.payload["task_ids"] == [uid("task")]


def test_photo_uploaded_locks_the_canvas() -> None:
    plan = _one(_event("photo.uploaded", payload={"canvas_id": uid("canvas")}))
    assert plan.kind == "photo.pipeline" and plan.lock_key == f"canvas:{uid('canvas')}"
    # a photo outside a canvas has no canvas lock (D-A-110)
    loose = _one(_event("photo.uploaded", payload={"canvas_id": None}))
    assert loose.lock_key == f"photo:{uid('photo')}"


def test_material_class_depends_on_the_session() -> None:
    inside = _event("material.uploaded", payload={"session_id": uid("s")})
    assert _one(inside).priority_class == "P1"
    outside = _event("material.uploaded", payload={"session_id": None})
    assert _one(outside).priority_class == "P2"


def test_bank_task_gets_a_solution_space_teacher_task_does_not() -> None:
    assert _kinds(_event("task.created", payload={"source": "seminar"})) == ["task.solution_space"]
    assert _kinds(_event("task.created", payload={"source": "teacher"})) == []


def test_agent_request_class_and_kind() -> None:
    event = _event("agent.requested", payload={"kind": "viz", "needed_in_session": True})
    plan = _one(event)
    assert plan.kind == "agent.viz" and plan.priority_class == "P1"
    later = _event("agent.requested", payload={"kind": "artifact", "needed_in_session": False})
    assert _one(later).priority_class == "P2"


def test_known_check_request_is_the_known_claim_check() -> None:
    """B's answer to D-A-110 p.7: `agent.requested {kind: known_check}` -> `check.known_claim`,
    P1 if needed in the session, else P2; keys as for `known.claimed`."""
    topic = uid("topic")
    params = {"topic_id": topic}
    now = _one(
        _event(
            "agent.requested",
            payload={"kind": "known_check", "params": params, "needed_in_session": True},
        )
    )
    claimed = _one(_event("known.claimed"))
    assert now.kind == claimed.kind == "check.known_claim"
    assert now.priority_class == "P1"
    assert (now.lock_key, now.coalesce_key) == (claimed.lock_key, claimed.coalesce_key)
    assert now.required_seq == 10
    assert now.payload["topic_id"] == str(topic)
    assert now.payload["request_id"] == str(uid("request"))
    later = _event(
        "agent.requested",
        payload={"kind": "known_check", "params": params, "needed_in_session": False},
    )
    assert _one(later).priority_class == "P2"


def test_known_check_request_without_topic_puts_no_job() -> None:
    """Review B of #117: `check.known_claim` checks the skills of a topic (WP8.2); a request
    without `params.topic_id` has nothing to check."""
    event = _event(
        "agent.requested",
        payload={"kind": "known_check", "params": {}, "needed_in_session": True},
    )
    assert _kinds(event) == []


def test_topic_split_request_is_a_graph_job_of_the_domain() -> None:
    """WP8.2 (D-A-226): `agent.requested {kind: topic_split}` -> `agent.topic_split`, a graph
    writer under the lock of the domain, the topic in the payload; without a topic no job."""
    topic = uid("topic")
    event = _event(
        "agent.requested",
        payload={
            "kind": "topic_split",
            "params": {"topic_id": topic},
            "needed_in_session": False,
        },
    )
    plan = _one(event)
    assert plan.kind == "agent.topic_split" and plan.priority_class == "P2"
    assert plan.lock_key == f"domain:{event.domain_id}" and plan.required_seq == 10
    assert plan.payload["topic_id"] == str(topic)
    bare = _event(
        "agent.requested",
        payload={"kind": "topic_split", "params": {}, "needed_in_session": False},
    )
    assert _kinds(bare) == []


def test_plan_update_cancels_agent_requests() -> None:
    """WP7.4, Т-§13.7: the teacher's `planUpdate.cancel_requests` cancels those requests."""
    first, second = UUID(int=31), UUID(int=32)
    plan_update = {
        **sample_payload("turn.committed")["markup"]["plan_update"],
        "cancel_request_ids": [str(first), str(second)],
    }
    markup = {**sample_payload("turn.committed")["markup"], "plan_update": plan_update}
    event = _event("turn.committed", payload={"markup": markup})
    assert r.cancelled_requests(event) == [first, second]
    assert r.cancelled_requests(_event("turn.committed")) == []
    assert r.cancelled_requests(_event("message.sent")) == []


def test_today_shown_coalesces_per_learner() -> None:
    plan = _one(_event("today.shown"))
    assert plan.kind == "today.prepare"
    assert plan.lock_key == plan.coalesce_key == f"today:{LEARNER}"
    assert plan.job_key == f"today:{LEARNER}:10"


def test_handwriting_known_claim_and_reextraction_rows() -> None:
    assert _one(_event("handwriting.corrected")).kind == "canvas.rediagnose"
    assert _one(_event("known.claimed")).kind == "check.known_claim"
    assert _one(_event("episode.reassigned")).kind == "extract.episode"
    # Т-§2.8: the episodes where the old skill has evidence are re-extracted, restricted to the
    # parts — one job per episode, a row with data beyond the event (D-A-135, D-A-162).
    split = _event("skill.split", seq=30)
    assert r.plan_jobs(split, Settings()) == []
    assert r.plan_with_facts(split, SETTINGS, r.RuleFacts()) == []  # no such episode: no job
    first, second = r.plan_with_facts(split, SETTINGS, r.RuleFacts(episode_seqs=(4, 9)))
    assert (first.kind, first.priority_class, first.required_seq) == ("extract.episode", "P2", 30)
    assert first.job_key != second.job_key  # one job per episode
    assert first.payload["episode_seq"] == 4 and second.payload["episode_seq"] == 9
    parts = [part["skill_id"] for part in split.payload["into"]]
    assert first.payload["restrict_skill_ids"] == parts
    assert "skill.split" not in {row.event_type for row in r.ROWS_NEED_FACTS}


def test_graph_hygiene_after_a_session_with_temporary_entities_and_every_n_topics() -> None:
    """Т-§13.3, Т-§4.10: P3, `domain:{id}` / `hygiene:{domain}`, reads the graph."""
    ended = _event("session.ended", seq=40)
    domain = uid("domain")
    plans = [
        p
        for p in r.plan_with_facts(ended, SETTINGS, r.RuleFacts(domain_id=UUID(domain)))
        if p.kind == "graph.hygiene"
    ]
    assert plans == []  # no temporary entities
    (plan,) = [
        p
        for p in r.plan_with_facts(
            ended, SETTINGS, r.RuleFacts(domain_id=UUID(domain), temporary_entities=True)
        )
        if p.kind == "graph.hygiene"
    ]
    assert (plan.priority_class, plan.visible, plan.required_seq) == ("P3", False, 40)
    assert plan.lock_key == f"domain:{domain}" and plan.coalesce_key == f"hygiene:{domain}"
    assert plan.job_key == f"hygiene:{domain}:40"
    # No domain known: nothing to clean.
    assert not [
        p
        for p in r.plan_with_facts(ended, SETTINGS, r.RuleFacts(temporary_entities=True))
        if p.kind == "graph.hygiene"
    ]

    created = _event("topic.created", seq=41)
    assert r.plan_with_facts(created, SETTINGS, r.RuleFacts()) == []
    (counted,) = r.plan_with_facts(created, SETTINGS, r.RuleFacts(hygiene_due=True))
    assert counted.kind == "graph.hygiene" and counted.coalesce_key == f"hygiene:{domain}"
    assert counted.payload["reason"] == "new_topics"


def test_embedding_rows_carry_the_text_and_read_no_graph() -> None:
    """D-A-117: the text rides in the job; the job reads only the log (`required_seq` empty)."""
    chunk = _one(_event("chunk.indexed"))
    assert chunk.kind == "embedding.compute" and chunk.required_seq is None
    assert chunk.payload["nodes"] == [
        {"node_kind": "chunk", "node_id": uid("chunk"), "text": "A limit is…"}
    ]
    (misconception,) = r.plan_jobs(_event("misconception.described"), SETTINGS)
    assert misconception.payload["nodes"] == [
        {
            "node_kind": "misconception",
            "node_id": uid("misconception"),
            "text": "limit equals the value",
        }
    ]
    # Topic, skill and evidence stay open: the text property is [ПРОВЕРИТЬ], evidence has no id.
    (open_row,) = [row for row in r.ROWS_NEED_FACTS if row.job_kinds == ("embedding.compute",)]
    assert open_row.event_type == "*"


def test_visible_kinds_follow_t_13_8() -> None:
    for kind in ("check.symbolic", "check.model", "material.parse", "photo.pipeline", "agent.viz"):
        assert kind in r.VISIBLE_KINDS
    for kind in ("extract.episode", "embedding.compute", "graph.hygiene", "analyst.run"):
        assert kind not in r.VISIBLE_KINDS
    assert "misconception.describe" not in r.VISIBLE_KINDS


def test_no_rule_for_events_outside_the_table() -> None:
    assert r.plan_jobs(_event("message.sent"), SETTINGS) == []


def test_artifact_topic_refresh_after_the_session_and_after_hints() -> None:
    """Т-§17.7, WP7.1, WP23.2 (D-A-136). `session.ended` with exposures or evidence on a topic —
    one `artifact.topic_refresh` per topic per session; `task.closed` where hints were needed —
    the traps block for the task's topics. Lock `artifact:{topic}`, P2, reads the
    graph. The topics are data beyond the event: the dispatcher gathers them (`RuleFacts`)."""
    first, second = UUID(int=0x71), UUID(int=0x72)
    ended = _event("session.ended", seq=40)
    plans = r.plan_with_facts(ended, SETTINGS, r.RuleFacts(topic_ids=(first, second)))
    refresh = [p for p in plans if p.kind == "artifact.topic_refresh"]
    assert [p.payload["topic_id"] for p in refresh] == [str(first), str(second)]
    plan = refresh[0]
    assert plan.lock_key == f"artifact:{first}"
    assert plan.coalesce_key == f"artifact:{first}:session:{ended.session_id}"
    assert plan.job_key == f"artifact.topic_refresh:{first}:session:{ended.session_id}"
    assert (plan.priority_class, plan.required_seq) == ("P2", 40)
    assert plan.payload == {"trigger_seq": 40, "topic_id": str(first), "reason": "session"}
    assert {"analyst.run", "analyst.deep"} <= {p.kind for p in plans}  # event-only rows stay
    nothing = r.plan_with_facts(ended, SETTINGS, r.RuleFacts())
    assert not [p for p in nothing if p.kind.startswith("artifact.")]

    closed = _event("task.closed", seq=41)
    task = closed.payload["task_id"]
    (trap,) = [
        p
        for p in r.plan_with_facts(closed, SETTINGS, r.RuleFacts(topic_ids=(first,)))
        if p.kind == "artifact.topic_refresh"
    ]
    assert trap.job_key == f"artifact.topic_refresh:{first}:task:{task}"
    assert trap.lock_key == f"artifact:{first}"
    # The branches must not coalesce into one queued job: `enqueue_job` would overwrite the
    # payload of the waiting one (#154, C): the session and the hints branches differ by key.
    assert trap.coalesce_key == f"artifact:{first}:task:{task}"
    assert trap.coalesce_key != plan.coalesce_key
    assert trap.payload == {
        "trigger_seq": 41,
        "topic_id": str(first),
        "reason": "hints",
        "task_id": task,
    }
    assert "artifact.topic_refresh" not in {
        row.job_kinds[0]
        for row in r.ROWS_NEED_FACTS
        if row.event_type in ("session.ended", "task.closed")
    }


def test_hub_deadline_set_puts_one_cheatsheet_job_without_facts() -> None:
    """Т-§17.7, WP23.5 (D-A-139, D-C-138): `hub.deadline_set` -> one `artifact.cheatsheet` on the
    hub, payload `{hub_id}`; the offer state is read by the job itself."""
    event = _event("hub.deadline_set", seq=50)
    (plan,) = [p for p in r.plan_jobs(event, SETTINGS) if p.kind == "artifact.cheatsheet"]
    hub = event.payload["hub_id"]
    assert plan.lock_key == plan.coalesce_key == f"artifact:{hub}"
    assert plan.priority_class == "P2" and plan.required_seq == 50
    assert plan.payload == {"trigger_seq": 50, "hub_id": hub}
    assert "artifact.cheatsheet" not in {row.job_kinds[0] for row in r.ROWS_NEED_FACTS}


def test_dispatcher_column_of_the_catalog_is_covered_by_rows_or_episodes() -> None:
    """T-D1 (the catalog side): a type marked «Диспетчер» in §6.3 has a row, or is an episode
    event (WP7.2), or is on the list of open marks below — nothing silently unhandled."""
    from quack.events.registry import SPECS
    from quack.jobs.episodes import EPISODE_TYPES

    rows = {row.event_type for row in r.RULES}
    marked = {key[0] for key, spec in SPECS.items() if spec.dispatcher}
    unhandled = marked - rows - EPISODE_TYPES
    # skill.created, topic.proposed — the open embedding row (the embedded property is
    # [ПРОВЕРИТЬ]); skill.merged, material.parsed — a mark without a row in Т-§13.3 / WP7.1
    # (D-A-162, question to the owner).
    assert unhandled == {"skill.created", "topic.proposed", "skill.merged", "material.parsed"}


def test_cheatsheet_consent_is_the_job_of_its_hub() -> None:
    """Т-§17.7 «генерация — после согласия» (D-A-199, #207): `agent.requested {kind: artifact,
    params: {artifact: cheatsheet, hub_id}}` puts `artifact.cheatsheet` of the hub with the
    request; without a hub — nothing; other artifacts stay `agent.artifact`."""
    hub = uid("hub")
    params = {"artifact": "cheatsheet", "hub_id": hub}
    plan = _one(
        _event("agent.requested",
               payload={"kind": "artifact", "params": params, "needed_in_session": False})
    )  # fmt: skip
    assert (plan.kind, plan.lock_key, plan.coalesce_key) == (
        "artifact.cheatsheet",
        f"artifact:{hub}",
        f"artifact:{hub}",
    )
    assert plan.payload["hub_id"] == hub and "request_id" in plan.payload
    no_hub = {"kind": "artifact", "params": {"artifact": "cheatsheet"}, "needed_in_session": False}
    assert r.plan_jobs(_event("agent.requested", payload=no_hub), SETTINGS) == []
    notes = {"kind": "artifact", "params": {"artifact": "notes"}, "needed_in_session": False}
    assert _one(_event("agent.requested", payload=notes)).kind == "agent.artifact"
