"""The body of the insurance job `check.symbolic` (Т-§6.1, Т-§22.7; ТЗ Ф4 WP11.3).

`run_at` = answer time + `symbolic_check_delay_ms` (dispatcher, seam S6). The job first looks
for the verdict by `verdict:{answer_seq}:{part}` — the inline check of the API may have written
it — and ends if it is there. Otherwise it checks with `symbolic_background_timeout_ms` and
writes the verdict, or `check.failed {answer_seq, part, reason}`, which the dispatcher turns
into `check.model`. The registration of the handler comes with the dispatcher of Phase 4
(stream A, WP7.1, G2).
"""

from __future__ import annotations

from uuid import UUID

from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import AnswerSubmitted
from quack.events.keys import check_failed_key, verdict_key
from quack.events.log import NewEvent, append_events
from quack.practice.answer_spec import LearnerAnswer
from quack.practice.answers import help_in_session, part_spec
from quack.practice.check import CheckFailed, HelpUsed, NeedsModel, NotYet, Verdict, check
from quack.practice.model_check import record_verdict
from quack.practice.repo import event_by_key, event_by_seq
from quack.practice.task_store import get_task

type InsuranceResult = Verdict | CheckFailed | NeedsModel | None


async def insure_symbolic(
    db: Db,
    compute: ComputeClient,
    settings: Settings,
    *,
    learner_id: UUID,
    answer_seq: int,
    part: str,
) -> InsuranceResult:
    """`None` — the verdict was already there; otherwise what was written (a `NeedsModel`
    is not written yet, D-B-146)."""
    if await event_by_key(db, learner_id=learner_id, key=verdict_key(answer_seq, part)):
        return None
    event = await event_by_seq(db, learner_id=learner_id, seq=answer_seq)
    if event is None or event.type != "answer.submitted":
        raise LookupError(f"no answer.submitted at seq {answer_seq}")
    payload = AnswerSubmitted.model_validate(event.payload)
    task = await get_task(db, learner_id=learner_id, task_id=payload.task_id)
    if task is None:
        raise LookupError(f"no task {payload.task_id}")
    help_used = (
        await help_in_session(
            db, learner_id=learner_id, session_id=event.session_id, task_id=payload.task_id
        )
        if event.session_id is not None
        else HelpUsed()
    )
    result = await check(
        LearnerAnswer.model_validate(payload.answer.model_dump()),
        await part_spec(db, learner_id=learner_id, task=task, part=part),
        answer_seq=answer_seq,
        part=part,
        settings=settings,
        compute=compute,
        timeout_ms=settings.symbolic_background_timeout_ms,
        help_used=help_used,
        recognition_confidence=payload.recognition_confidence,
    )
    if isinstance(result, Verdict):
        await record_verdict(
            db,
            result,
            learner_id=learner_id,
            session_id=event.session_id,
            domain_id=event.domain_id,
        )
        return result
    if isinstance(result, NeedsModel):
        # D-B-146: no structural check for the form — `unsupported`; S6 `check.failed →
        # check.model` gives it to the model (Т-§6.6).
        failed = CheckFailed(answer_seq=answer_seq, part=part, reason="unsupported")
    elif isinstance(result, NotYet):
        failed = CheckFailed(answer_seq=answer_seq, part=part, reason=result.reason)
    else:
        failed = result
    await append_events(
        db,
        learner_id,
        [
            NewEvent(
                type="check.failed",
                payload=failed,
                actor="code",
                idempotency_key=check_failed_key(answer_seq, part),
                domain_id=event.domain_id,
                session_id=event.session_id,
                causation_seq=answer_seq,
            )
        ],
    )
    return failed
