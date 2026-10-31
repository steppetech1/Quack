"""Practice engine (ТЗ §5 п.2, §2.2): task selection and generation, the hint ladder and the
hybrid evaluation cascade (decision 1, A1, A7–A12)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, LiteralString
from uuid import UUID, uuid4, uuid5

import httpx
from neo4j import READ_ACCESS, AsyncDriver
from sqlalchemy import text

from quack.db.postgres import SessionFactory
from quack.events.payloads import MisconceptionEvalHit, SkillEvalScore
from quack.graph.ids import QUACK_UUID_NS
from quack.llm.errors import ProviderUnavailableError
from quack.practice.evaluation import FEEDBACK_INCORRECT
from quack.practice.generation import generated_family_id
from quack.practice.schemas import (
    EvaluatorMisconception,
    EvaluatorOutput,
    RubricItemCheck,
    TaskAnswer,
    TaskGenOutput,
    TaskLevel,
)

if TYPE_CHECKING:
    from tests.artifacts.conftest import (
        AuthHeaders,
        FakeCompute,
        FakeProvider,
        Seeder,
        Structure,
    )

HINTS = [
    "Treat sin(x**2) as a composite function.",
    "Multiply the derivative of the outer sine by the derivative of x**2.",
    "cos(x**2) times 2*x.",
]
CHAIN_REF: dict[str, Any] = {
    "expression": "2*x*cos(x**2)",
    "latex": "2x\\cos(x^2)",
    "mode": "expression_equiv",
    "variables": ["x"],
    "hints": HINTS,
}
# A non-default check mode and variable: the request to compute must take them from the task.
INTEGRAL_REF: dict[str, Any] = {
    "expression": "t**3/3",
    "latex": "\\frac{t^3}{3}",
    "mode": "integral",
    "variables": ["t"],
    "hints": ["Use the power rule.", "Raise the power by one.", "t**3 divided by 3."],
}


def _compute_answer(status: str, is_equivalent: bool | None) -> dict[str, Any]:
    return {
        "status": status,
        "is_equivalent": is_equivalent,
        "normalized_student": None,
        "diff_hint": None,
        "error_message": "unexpected end of input" if status == "parse_error" else None,
    }


# --- queries ----------------------------------------------------------------------------


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _last_seq(db: SessionFactory, learner: UUID) -> int:
    [row] = await _q(db, "SELECT last_seq FROM core.learner_seq WHERE learner_id = :l", l=learner)
    return int(row["last_seq"])


async def _events_after(db: SessionFactory, learner: UUID, seq: int) -> list[dict[str, Any]]:
    return await _q(
        db,
        "SELECT seq, type, actor, session_id, payload FROM core.events "
        "WHERE learner_id = :l AND seq > :s ORDER BY seq",
        l=learner,
        s=seq,
    )


async def _events_of(db: SessionFactory, learner: UUID, type_: str) -> list[dict[str, Any]]:
    return await _q(
        db,
        "SELECT seq, type, actor, session_id, payload FROM core.events "
        "WHERE learner_id = :l AND type = :t ORDER BY seq",
        l=learner,
        t=type_,
    )


async def _graph(neo4j: AsyncDriver, cypher: LiteralString, **params: Any) -> list[dict[str, Any]]:
    async with neo4j.session(default_access_mode=READ_ACCESS) as session:
        result = await session.run(cypher, params)
        return [dict(record) async for record in result]


async def _skill(neo4j: AsyncDriver, learner: UUID, skill_id: UUID) -> dict[str, Any]:
    [row] = await _graph(
        neo4j,
        "MATCH (s:Skill {id: $id}) WHERE s.learnerId = $l RETURN properties(s) AS p",
        id=str(skill_id),
        l=str(learner),
    )
    return row["p"]


async def _evidence_tiers(neo4j: AsyncDriver, learner: UUID, seq: int) -> list[int]:
    """Tiers of the skill evidence created from the event with this seq (phase_a `tier`)."""
    rows = await _graph(
        neo4j,
        "MATCH (e:Evidence) WHERE e.learnerId = $l AND e.sourceSeq = $seq AND e.kind = 'skill' "
        "RETURN e.tier AS tier",
        l=str(learner),
        seq=seq,
    )
    return [row["tier"] for row in rows]


# --- seeding ----------------------------------------------------------------------------


async def _practice_history(seed: Seeder) -> tuple[UUID, Structure, UUID]:
    """Skill A solved by one `fam-a` task in a finished session, skill B untouched; a new
    session is open and the projector has applied everything."""
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    past = await seed.start_session(learner, s.domain_id, s.topic_id)
    solved = await seed.seed_bank_task(
        learner, s, skill_id=s.skill_a, family="fam-a", kind="math", reference_solution=CHAIN_REF
    )
    await seed.solve_task(learner, past, solved, s.topic_id, s.skill_a)
    await seed.complete_session(learner, past, s.domain_id)
    sid = await seed.start_session(learner, s.domain_id, s.topic_id)
    await seed.project(learner)
    return learner, s, sid


async def _open_session(seed: Seeder) -> tuple[UUID, Structure, UUID]:
    learner = await seed.learner()
    s = await seed.seed_structure(learner)
    sid = await seed.start_session(learner, s.domain_id, s.topic_id)
    return learner, s, sid


async def _assigned_task(
    seed: Seeder, learner: UUID, s: Structure, sid: UUID, reference: dict[str, Any]
) -> UUID:
    task = await seed.seed_bank_task(
        learner, s, skill_id=s.skill_a, family="fam-a", kind="math", reference_solution=reference
    )
    await seed.assign_task(learner, sid, task, s.topic_id)
    return task


# --- API calls --------------------------------------------------------------------------


async def _hint(
    client: httpx.AsyncClient, headers: dict[str, str], sid: UUID, task: UUID, body: dict[str, Any]
) -> dict[str, Any]:
    r = await client.post(f"/v1/sessions/{sid}/tasks/{task}/hint", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _hint_body(key: str) -> dict[str, Any]:
    return {"turn_id": str(uuid4()), "idempotency_key": key}


async def _submit(
    client: httpx.AsyncClient, headers: dict[str, str], sid: UUID, task: UUID, answer: str, key: str
) -> dict[str, Any]:
    body = {"turn_id": str(uuid4()), "answer": answer, "idempotency_key": key}
    r = await client.post(f"/v1/sessions/{sid}/tasks/{task}/submit", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# --- tests ------------------------------------------------------------------------------


async def test_next_task_targets_new_family(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    seed: Seeder,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_provider: FakeProvider,
) -> None:
    learner, s, sid = await _practice_history(seed)
    skill_a = await _skill(neo4j, learner, s.skill_a)
    skill_b = await _skill(neo4j, learner, s.skill_b)
    assert skill_a["distinctFamilies"] == 1 and skill_a["masteryStatus"] != "mastered"
    assert skill_b["evidenceCount"] == 0
    fake_provider.respond(
        "TaskGenOutput",
        TaskGenOutput(
            statement="Differentiate $\\sin(x^2)$.",
            options=[],
            answer=TaskAnswer(type="expression", value="2*x*cos(x**2)", units=None),
            solution="By the chain rule the derivative is $2x\\cos(x^2)$.",
            hints=HINTS,
            tests=[TaskLevel(skill="S1", level=3)],
            method_named=False,
            variant_of=None,
            form=None,
            pair_task=None,
        ),
    )
    before = await _last_seq(db, learner)
    body = {
        "topic_id": str(s.topic_id),
        "turn_id": str(uuid4()),
        "kind": "math",
        "idempotency_key": "next-1",
    }

    r = await client.post(f"/v1/sessions/{sid}/tasks/next", json=body, headers=auth(learner))
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["degraded"] is False
    assert task["kind"] == "math"
    assert task["task_id"] == str(uuid5(QUACK_UUID_NS, f"task:{learner}:{sid}:next-1"))  # A11
    # D-B-010: every generated task is its own family, derived by code from its id.
    family = generated_family_id(UUID(task["task_id"]))
    assert task["family_id"] == family

    # A7: skill A (distinctFamilies == 1) is the target; A8: its solved family is excluded.
    [call] = fake_provider.calls_for("TaskGenOutput")
    # WP14.1: the model sees the target skill as a label with its name, never its id.
    assert f"S1: {skill_a['name']}" in call.prompt
    assert str(s.skill_a) not in call.prompt
    assert skill_b["name"] not in call.prompt
    assert "fam-a" in call.prompt

    new = await _events_after(db, learner, before)
    assert [(e["type"], e["actor"]) for e in new] == [
        ("task.generated", "task_gen@1"),
        ("task.assigned", "system"),
    ]
    generated, assigned = new
    assert generated["payload"]["task_id"] == assigned["payload"]["task_id"] == task["task_id"]
    assert generated["payload"]["family_id"] == family
    assert [t["skill"]["skill_id"] for t in generated["payload"]["target_skills"]] == [
        str(s.skill_a)
    ]
    assert assigned["session_id"] == sid
    [bank] = await _q(
        db,
        "SELECT family_id, kind, reference_solution FROM core.task_bank "
        "WHERE id = :t AND learner_id = :l",
        t=UUID(task["task_id"]),
        l=learner,
    )
    assert (bank["family_id"], bank["kind"]) == (family, "math")
    assert bank["reference_solution"]["hints"] == HINTS
    assert bank["reference_solution"]["expression"] == CHAIN_REF["expression"]
    assert bank["reference_solution"]["mode"] == CHAIN_REF["mode"]

    # A10: the same idempotency_key returns the stored answer without new events or LLM calls.
    again = await client.post(f"/v1/sessions/{sid}/tasks/next", json=body, headers=auth(learner))
    assert again.status_code == 200, again.text
    assert again.json() == task
    assert await _last_seq(db, learner) == before + 2
    assert len(fake_provider.calls) == 1

    # T-E2: the same key with another body is 409 and writes nothing.
    for other in ({**body, "kind": "open"}, {**body, "turn_id": str(uuid4())}):
        r = await client.post(f"/v1/sessions/{sid}/tasks/next", json=other, headers=auth(learner))
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _last_seq(db, learner) == before + 2


async def test_next_task_falls_back_to_bank_when_llm_down(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    seed: Seeder,
    db: SessionFactory,
    fake_provider: FakeProvider,
) -> None:
    learner, s, sid = await _practice_history(seed)
    bank_task = await seed.seed_bank_task(
        learner, s, skill_id=s.skill_a, family="fam-a", kind="math", reference_solution=CHAIN_REF
    )
    fake_provider.respond("TaskGenOutput", ProviderUnavailableError("provider returned HTTP 503"))
    before = await _last_seq(db, learner)
    body = {
        "topic_id": str(s.topic_id),
        "turn_id": str(uuid4()),
        "kind": "math",
        "idempotency_key": "next-1",
    }

    r = await client.post(f"/v1/sessions/{sid}/tasks/next", json=body, headers=auth(learner))
    assert r.status_code == 200, r.text
    task = r.json()
    assert task["degraded"] is True
    assert task["task_id"] == str(bank_task)
    assert task["family_id"] == "fam-a"
    assert len(fake_provider.calls_for("TaskGenOutput")) == 1  # the LLM was tried first

    new = await _events_after(db, learner, before)
    assert [e["type"] for e in new] == ["task.assigned"]
    assert new[0]["payload"]["task_id"] == str(bank_task)
    assert new[0]["session_id"] == sid


async def test_hints_progress_and_reduce_independence(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    seed: Seeder,
    db: SessionFactory,
    fake_compute: FakeCompute,
) -> None:
    learner, s, sid = await _open_session(seed)
    headers = auth(learner)
    task = await _assigned_task(seed, learner, s, sid, CHAIN_REF)

    bodies = [_hint_body(f"hint-{level}") for level in (1, 2, 3)]
    hints = [await _hint(client, headers, sid, task, body) for body in bodies]
    # Decision 1 (Т-§7.4): the penalty of level k is 0.25·k.
    assert [(h["hint_level"], h["hint_text"], h["independence_penalty"]) for h in hints] == [
        (1, HINTS[0], 0.25),
        (2, HINTS[1], 0.5),
        (3, HINTS[2], 0.75),
    ]
    assert all(h["task_id"] == str(task) for h in hints)

    count = await _last_seq(db, learner)
    assert await _hint(client, headers, sid, task, bodies[2]) == hints[2]  # A10
    assert await _last_seq(db, learner) == count
    # T-E2: the same key with another turn is 409 and writes nothing.
    other = {**bodies[2], "turn_id": str(uuid4())}
    r = await client.post(f"/v1/sessions/{sid}/tasks/{task}/hint", json=other, headers=headers)
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _last_seq(db, learner) == count

    fourth = await _hint(client, headers, sid, task, _hint_body("hint-4"))
    assert (fourth["hint_level"], fourth["hint_text"], fourth["independence_penalty"]) == (
        3,
        HINTS[2],
        0.75,
    )
    given = await _events_of(db, learner, "task.hint_given")
    assert [e["payload"]["hint_level"] for e in given] == [1, 2, 3, 3]
    assert [e["seq"] for e in given] == [h["seq"] for h in [*hints, fourth]]

    # independence = 1 − max(penalties): the repeated level-3 hint does not lower it further.
    fake_compute.respond(_compute_answer("completed", True))
    result = await _submit(client, headers, sid, task, "2*x*cos(x**2)", "submit-1")
    assert result["independence"] == 0.25
    [submitted] = await _events_of(db, learner, "task.submitted")
    assert submitted["payload"]["hints_used"] == 4

    second = await _assigned_task(seed, learner, s, sid, CHAIN_REF)
    for key in ("second-hint-1", "second-hint-2"):
        await _hint(client, headers, sid, second, _hint_body(key))
    result = await _submit(client, headers, sid, second, "2*x*cos(x**2)", "submit-2")
    assert result["independence"] == 0.5

    third = await _assigned_task(seed, learner, s, sid, CHAIN_REF)
    await _hint(client, headers, sid, third, _hint_body("third-hint-1"))
    result = await _submit(client, headers, sid, third, "2*x*cos(x**2)", "submit-3")
    assert result["independence"] == 0.75


async def test_submit_math_uses_compute_tier1(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    seed: Seeder,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_provider: FakeProvider,
    fake_compute: FakeCompute,
) -> None:
    learner, s, sid = await _open_session(seed)
    task = await _assigned_task(seed, learner, s, sid, INTEGRAL_REF)
    fake_compute.respond(_compute_answer("completed", True))
    before = await _last_seq(db, learner)

    result = await _submit(client, auth(learner), sid, task, "t^3/3 + 5", "submit-1")
    assert result["task_id"] == str(task)
    assert result["evaluator"] == "compute"
    assert result["is_correct"] is True
    assert result["score"] == 1.0
    assert result["independence"] == 1.0
    assert [x["skill_id"] for x in result["skill_scores"]] == [str(s.skill_a)]
    assert result["skill_scores"][0]["tested_depth"] == 0.5  # decision 8

    count = await _last_seq(db, learner)
    body = {"turn_id": str(uuid4()), "answer": "t^2", "idempotency_key": "submit-1"}
    r = await client.post(
        f"/v1/sessions/{sid}/tasks/{task}/submit", json=body, headers=auth(learner)
    )
    assert r.status_code == 409, r.text  # T-E2: the same key with another answer
    assert r.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert await _last_seq(db, learner) == count

    submitted, evaluated = await _events_after(db, learner, before)
    assert (submitted["type"], submitted["actor"]) == ("task.submitted", "learner")
    assert (evaluated["type"], evaluated["actor"]) == ("task.evaluated", "code")
    assert evaluated["seq"] == submitted["seq"] + 1
    assert fake_provider.calls == []

    # A1: mode, reference and variables come from reference_solution.
    [request] = fake_compute.requests
    assert request["mode"] == "integral"
    assert request["reference_expr"] == "t**3/3"
    assert request["variables"] == ["t"]
    assert request["student_expr"] == "t^3/3 + 5"

    await seed.project(learner)
    assert await _evidence_tiers(neo4j, learner, evaluated["seq"]) == [1]


async def test_submit_falls_back_to_evaluator_on_parse_error(
    client: httpx.AsyncClient,
    auth: AuthHeaders,
    seed: Seeder,
    db: SessionFactory,
    neo4j: AsyncDriver,
    fake_provider: FakeProvider,
    fake_compute: FakeCompute,
) -> None:
    learner, s, sid = await _open_session(seed)
    task = await _assigned_task(seed, learner, s, sid, CHAIN_REF)
    fake_compute.respond(_compute_answer("parse_error", None))
    # D-B-009: the verdict of `evaluator@1` is mapped by code to the fields of `task.evaluated`.
    verdict = EvaluatorOutput(
        items=[RubricItemCheck(item="R1", status="wrong", quote="2x cos(x^2")],
        verdict="incorrect",
        confidence=0.8,
        misconception=EvaluatorMisconception(
            label=f"new: {s.misconception_a_name}", quote="2x cos(x^2"
        ),
        note="The inner derivative is applied to the wrong factor.",
    )
    fake_provider.respond("EvaluatorOutput", verdict)
    before = await _last_seq(db, learner)

    result = await _submit(client, auth(learner), sid, task, "2x cos(x^2", "submit-1")
    expected = {
        "is_correct": False,
        "score": 0.0,
        "feedback": FEEDBACK_INCORRECT,
        "skill_scores": [
            SkillEvalScore(skill_id=s.skill_a, score=0.0, tested_depth=0.5).model_dump(mode="json")
        ],
        "triggered_misconceptions": [
            MisconceptionEvalHit(skill_id=s.skill_a, name=s.misconception_a_name).model_dump(
                mode="json"
            )
        ],
    }
    assert result["evaluator"] == "hybrid"
    for key in ("is_correct", "score", "feedback", "skill_scores", "triggered_misconceptions"):
        assert result[key] == expected[key], key
    assert len(fake_compute.requests) == 1
    assert len(fake_provider.calls_for("EvaluatorOutput")) == 1

    submitted, evaluated = await _events_after(db, learner, before)
    assert submitted["type"] == "task.submitted"
    assert (evaluated["type"], evaluated["actor"]) == ("task.evaluated", "evaluator@1")
    assert evaluated["payload"]["evaluator"] == "hybrid"

    await seed.project(learner)
    assert await _evidence_tiers(neo4j, learner, evaluated["seq"]) == [2]
