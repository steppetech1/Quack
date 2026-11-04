"""E2E: the shared input field, the summary of a session and the return after the sweep
(ТЗ Ф4 §8.14, T-E13; WP8.3, WP13.10, WP16.4).

The text goes to `POST /v1/domains/classify` (nothing is written), the learner confirms the
suggested domain by starting a session in it, two tasks are answered correctly, the summary
`GET /v1/sessions/{id}/summary` shows the skill of the solved tasks in codes before and after, and
an action after the sweep of inactive sessions brings the session back with
`session.started {resumed: true}`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from quack.jobs.runner import JobContext
from quack.jobs.types import ClaimedJob
from quack.session.lifecycle import sweep_inactive_sessions
from tests.e2e.journey import ok, onboard
from tests.e2e.system import System, job_context
from tests.e2e.text_journey import (
    act,
    answer_latest_task,
    event_types,
    script_task_checker,
    script_teacher,
    start_session,
)


async def test_classify_and_summary_journey(system: System) -> None:
    """T-E13."""
    script_task_checker(system)
    script_teacher(system, (True, False, True, False))
    system.llm.respond(
        "DomainClassifierOutput",
        lambda _r: {"decision": "existing", "domain": "D1", "new_name": None, "confidence": 0.95},
    )
    learner = await system.sign_in_by_link()
    domain_id, topic_id = await onboard(system, learner, "te13")
    before_classify = await event_types(system, learner)

    suggestion = ok(
        await system.client.post(
            "/v1/domains/classify",
            json={"text": "help me with the sum of a geometric series"},
            headers=learner.headers,
        )
    )
    assert suggestion["decision"] == "existing" and suggestion["domain_id"] == str(domain_id)
    assert await event_types(system, learner) == before_classify  # nothing is written

    session_id = await start_session(system, learner, domain_id, topic_id)
    for text in ("Explain the sum of a series", "Give me one more task"):
        message = await act(system, learner, session_id, {"kind": "message", "text": text})
        assert message.status_code == 200, message.text
        await answer_latest_task(system, learner, session_id)
    await system.settle()

    summary = ok(
        await system.client.get(f"/v1/sessions/{session_id}/summary", headers=learner.headers)
    )
    [change] = summary["skills"]
    assert change["changed"] is True and change["before"] != change["after"], change
    after = {level["level"]: level["status"] for level in change["after"]["levels"]}
    before = {level["level"]: level["status"] for level in change["before"]["levels"]}
    assert after[2] == "proven" and before[2] != "proven", (before, after)
    assert [(t["answers"], t["last_outcome"]) for t in summary["tasks"]] == [(1, "correct")] * 2

    # The sweep of inactive sessions ends the session; the next action brings it back.
    await system.resources.db.execute(
        "UPDATE content.sessions SET last_activity_at = %(old)s WHERE session_id = %(session_id)s",
        {"old": datetime.now(UTC) - timedelta(days=1), "session_id": session_id},
    )
    ctx: JobContext = job_context(system.resources)
    await sweep_inactive_sessions(cast(ClaimedJob, None), ctx)
    types = [t for _, t in await event_types(system, learner)]
    assert types.count("session.ended") == 1
    again = await act(system, learner, session_id, {"kind": "message", "text": "I am back"})
    assert again.status_code == 200, again.text
    rows = await system.resources.db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'session.started' ORDER BY seq",
        {"learner_id": learner.learner_id},
    )
    assert [r["payload"].get("resumed") for r in rows][-1] is True, rows
