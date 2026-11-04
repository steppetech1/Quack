"""Routes of stream B (ТЗ Ф4 §7.3): sessions, actions, `domains/classify`.

Stages 130-180 and 290: the learner's session. The session routes run on the Phase 4 log
(`content.sessions`, D-B-210): its domain is a `domain.created` of that log, made by `prepare`
(the structure steps of stream A still write the Phase 3 stand).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from quack.events.event_types import DomainCreated
from quack.events.log import NewEvent, append_events
from tests.e2e.routes import NO_ID, Ids, Route, Stream

if TYPE_CHECKING:
    from tests.e2e.system import Learner, System


async def _session_domain(system: System, learner: Learner, ids: Ids) -> Ids:
    """A domain of the Phase 4 log for the session of the learner."""
    domain_id = uuid4()
    created = NewEvent(
        type="domain.created",
        payload=DomainCreated(domain_id=domain_id, name="Sessions"),
        actor="learner",
        idempotency_key=f"te2:domain:{domain_id}",
    )
    await append_events(system.resources.db, learner.learner_id, [created])
    return ids.replace(session_domain_id=domain_id)


async def _started_session(system: System, learner: Learner, ids: Ids, payload: Any) -> Ids:
    return ids.replace(session_id=UUID(payload["session_id"]))


STREAM = Stream(
    name="b",
    # `task_id` and `turn_id` are used by the routes of stream C (cards, canvas).
    ids={
        "session_id": NO_ID,
        "session_domain_id": NO_ID,
        "task_id": NO_ID,
        "turn_id": NO_ID,
        "viz_id": NO_ID,
    },
    routes=(
        Route(
            "POST",
            "/v1/sessions",
            stage=130,
            body_key="uuid",
            body=lambda ids, v: {
                "domain_id": str(ids.session_domain_id),
                "intent": ["learn", "practice"][v],
            },
            prepare=_session_domain,
            advance=_started_session,
        ),
        # ТЗ Ф4 WP13.1: an action without a teacher turn answers `201 {seq}` (D-B-156); the
        # key is a UUID.
        Route(
            "POST",
            "/v1/sessions/{session_id}/actions",
            stage=180,
            requires=("session_id",),
            body_key="uuid",
            body=lambda ids, v: {"kind": "confidence", "target_seq": 1, "value": [2, 3][v]},
        ),
        Route(
            "POST",
            "/v1/sessions/{session_id}/end",
            requires=("session_id",),
            body_key="none",
            body=lambda ids, v: {},
            no_key="WP13.10: the end is keyed by the session, a repeat writes nothing new",
        ),
        Route(
            "POST",
            "/v1/sessions/{session_id}/heartbeat",
            no_key="no event; refreshes presence only",
            body_key="none",
            body=lambda ids, v: {},
        ),
        # ТЗ Ф4 WP16.4: the body is `{text | material_id}` only; a direct model call, nothing is
        # written (D-B-173).
        Route(
            "POST",
            "/v1/domains/classify",
            no_key="no event; a direct classifier call that writes nothing",
        ),
    ),
    other_routes=frozenset(
        {
            ("GET", "/v1/sessions"),
            ("GET", "/v1/sessions/active"),
            ("GET", "/v1/sessions/{session_id}"),
            ("GET", "/v1/sessions/{session_id}/events"),
            ("GET", "/v1/sessions/{session_id}/jobs"),
            ("GET", "/v1/llm/access"),
            ("GET", "/v1/viz/{viz_id}"),
        }
    ),
    routers=("quack.session.router:router", "quack.artifacts.viz_routes:router"),
)
