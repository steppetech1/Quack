"""ТЗ Ф4 §8.7 T-CH2, T-CH3 (insurance part): the body of the job `check.symbolic` (WP11.3):
it looks for the verdict by `verdict:{answer_seq}:{part}`; none — checks with
`symbolic_background_timeout_ms` and writes the verdict, or `check.failed` (Т-§6.1, Т-§22.7).
The job registration is the dispatcher of Phase 4 (A, WP7.1, G2)."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from quack.compute_client.math_contracts import MathCheckResponse
from quack.config import Settings
from quack.db.db import Db
from quack.events import records
from quack.events.event_types import AnswerSubmitted
from quack.events.keys import check_failed_key, verdict_key
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerSpec, CheckFailed, LearnerAnswer, MathValue
from quack.practice.check import Verdict
from quack.practice.model_check import record_verdict
from quack.practice.role_outputs import TaskCondition
from quack.practice.symbolic_job import insure_symbolic
from quack.practice.task_store import create_task
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute

SETTINGS = Settings()
CHECK_PATH = "/v1/math/check"
SPEC = AnswerSpec(type="expression", value=MathValue(latex="2x", sympy="2*x"))


async def _answer(db: Db, learner_id: UUID) -> int:
    task_id = uuid4()
    await create_task(
        db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=task_id,
        source="teacher",
        condition=TaskCondition(text="Simplify x + x", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=uuid4(), level=2)],
            method_named=False,
            answer=SPEC,
        ),
        verification="unchecked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
    )
    payload = AnswerSubmitted(
        task_id=task_id,
        answer=LearnerAnswer(kind="expression", value="x+x", sympy="x + x"),
        source="chat",
    )
    [event] = await append_events(
        db,
        learner_id,
        [NewEvent(type="answer.submitted", payload=payload, actor="learner", idempotency_key="a1")],
    )
    return event.seq


async def _types(db: Db, learner_id: UUID) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT type, idempotency_key, payload FROM core.events "
        "WHERE learner_id = %(l)s AND type IN ('verdict.recorded', 'check.failed') ORDER BY seq",
        {"l": learner_id},
    )


async def _insure(db: Db, learner_id: UUID, seq: int, *answers: MathCheckResponse | int) -> Any:
    scenario = ComputeScenario()
    scenario.push_for(CHECK_PATH, *answers)
    client = scripted_compute(scenario)
    try:
        result = await insure_symbolic(
            db, client, SETTINGS, learner_id=learner_id, answer_seq=seq, part="main"
        )
    finally:
        await client.aclose()
    sent = [r for r in scenario.requests if r.url.path == CHECK_PATH]
    return result, sent


async def test_writes_the_verdict_with_background_timeout(pg_db: Db) -> None:
    learner_id = await create_learner(pg_db)
    seq = await _answer(pg_db, learner_id)
    ok = MathCheckResponse(status="completed", outcome="correct", method="symbolic")
    result, sent = await _insure(pg_db, learner_id, seq, ok)
    assert isinstance(result, Verdict) and result.outcome == "correct"
    assert b'"timeout_ms":%d' % SETTINGS.symbolic_background_timeout_ms in sent[0].content
    rows = await _types(pg_db, learner_id)
    assert [(r["type"], r["idempotency_key"]) for r in rows] == [
        ("verdict.recorded", verdict_key(seq, "main"))
    ]


async def test_existing_verdict_ends_the_job(pg_db: Db) -> None:
    """T-CH2: no duplicates — the inline check already wrote the verdict."""
    learner_id = await create_learner(pg_db)
    seq = await _answer(pg_db, learner_id)
    inline = Verdict(
        answer_seq=seq, part="main", outcome="correct", method="symbolic", verification="code"
    )
    await record_verdict(pg_db, inline, learner_id=learner_id, session_id=None, domain_id=None)
    result, sent = await _insure(pg_db, learner_id, seq)
    assert result is None and sent == []
    assert len(await _types(pg_db, learner_id)) == 1


async def test_unparsed_writes_check_failed(pg_db: Db) -> None:
    """T-CH3, Т-§22.7: `check.failed`, not `unclear`."""
    learner_id = await create_learner(pg_db)
    seq = await _answer(pg_db, learner_id)
    failed = MathCheckResponse(status="failed", reason="unparsed")
    await _insure(pg_db, learner_id, seq, failed)
    rows = await _types(pg_db, learner_id)
    assert [(r["type"], r["idempotency_key"], r["payload"]["reason"]) for r in rows] == [
        ("check.failed", check_failed_key(seq, "main"), "unparsed")
    ]


async def test_timeout_or_compute_down_is_check_failed(pg_db: Db) -> None:
    """D-B-126: what the API left as «not yet» the insurance writes as `check.failed`."""
    learner_id = await create_learner(pg_db)
    seq = await _answer(pg_db, learner_id)
    await _insure(pg_db, learner_id, seq, 504)
    rows = await _types(pg_db, learner_id)
    assert [(r["type"], r["payload"]["reason"]) for r in rows] == [("check.failed", "timeout")]


async def test_unsupported_form_writes_check_failed_for_the_model(pg_db: Db) -> None:
    """D-B-146 (A answered in #117): a form without a structural check is
    `check.failed {reason: unsupported}`; the S6 row `check.failed → check.model` takes it
    (Т-§6.6 «нет её — проверка моделью»)."""
    learner_id = await create_learner(pg_db)
    seq = await _answer(pg_db, learner_id)
    unsupported = MathCheckResponse(status="failed", reason="unsupported")
    result, _ = await _insure(pg_db, learner_id, seq, unsupported)
    assert isinstance(result, CheckFailed) and result.reason == "unsupported"
    rows = await _types(pg_db, learner_id)
    assert [(r["type"], r["idempotency_key"], r["payload"]["reason"]) for r in rows] == [
        ("check.failed", check_failed_key(seq, "main"), "unsupported")
    ]


async def test_the_insurance_checks_a_part_by_the_spec_of_that_part(pg_db: Db) -> None:
    """Т-§6.2: the answer to part b is checked against `parts[b].answer`, not the key of the
    whole task (compute got the key of the whole task, so a right part b was `incorrect`)."""
    learner_id = await create_learner(pg_db)
    task_id = uuid4()
    await create_task(
        pg_db,
        learner_id=learner_id,
        task_id=task_id,
        family_id=task_id,
        source="teacher",
        condition=TaskCondition(text="a) Simplify x + x b) Simplify x * x", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=uuid4(), level=2)],
            method_named=False,
            answer=SPEC,
            parts=[
                records.TaskPartRecord(part="a", answer=SPEC),
                records.TaskPartRecord(
                    part="b",
                    answer=AnswerSpec(
                        type="expression", value=MathValue(latex="x^2", sympy="x**2")
                    ),
                ),
            ],
        ),
        verification="unchecked",
        actor="teacher@2",
        idempotency_key=f"task:{task_id}",
    )
    payload = AnswerSubmitted(
        task_id=task_id,
        part="b",
        answer=LearnerAnswer(kind="expression", value="x*x", sympy="x * x"),
        source="chat",
    )
    [event] = await append_events(
        pg_db,
        learner_id,
        [NewEvent(type="answer.submitted", payload=payload, actor="learner", idempotency_key="p1")],
    )
    scenario = ComputeScenario()
    scenario.push_for(
        CHECK_PATH, MathCheckResponse(status="completed", outcome="correct", method="symbolic")
    )
    client = scripted_compute(scenario)
    try:
        result = await insure_symbolic(
            pg_db, client, SETTINGS, learner_id=learner_id, answer_seq=event.seq, part="b"
        )
    finally:
        await client.aclose()

    sent = [r for r in scenario.requests if r.url.path == CHECK_PATH]
    assert b"x**2" in sent[0].content and b"2*x" not in sent[0].content
    assert isinstance(result, Verdict) and (result.part, result.outcome) == ("b", "correct")
