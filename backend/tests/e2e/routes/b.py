"""Routes of stream B (ТЗ Ф4 §7.3): sessions, actions, `domains/classify`, code tasks (Т-§6.10)
and the demo map of a domain.

Stages 130-180 and 290: the learner's session. The session routes run on the Phase 4 log
(`content.sessions`, D-B-210): its domain is a `domain.created` of that log, made by `prepare`
(the structure steps of stream A still write the Phase 3 stand).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from quack.events import records
from quack.events.event_types import DomainCreated
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerSpec
from quack.practice.code_tests import PythonCase, PythonTests
from quack.practice.role_outputs import TaskCondition
from quack.practice.task_store import create_task
from tests.e2e.routes import NO_ID, Ids, Route, Stream
from tests.support.runner import fake_runner

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


async def _code_task(system: System, learner: Learner, ids: Ids) -> Ids:
    """A ready `code` task of the learner and a runner that passes every test."""
    task_id = uuid4()
    tests = PythonTests(
        function="square",
        reference="def square(x):\n    return x * x\n",
        cases=[PythonCase(args=[2], expected=4, hidden=False)],
    )
    await create_task(
        system.resources.db,
        learner_id=learner.learner_id,
        task_id=task_id,
        family_id=task_id,
        source="generated",
        condition=TaskCondition(text="Square a number.", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=uuid4(), level=3)],
            method_named=False,
            answer=AnswerSpec(type="code", language="python", function="square"),
        ),
        verification="checked",
        actor="code_task_generator@1",
        idempotency_key=f"te2:code:{task_id}",
        code_tests=tests.model_dump(mode="json"),
    )
    system.resources.runner = fake_runner()
    return ids.replace(code_task_id=task_id)


_CODE_PATH_IDS = {"task_id": "code_task_id"}

STREAM = Stream(
    name="b",
    # `task_id` and `turn_id` are used by the routes of stream C (cards, canvas).
    ids={
        "session_id": NO_ID,
        "session_domain_id": NO_ID,
        "task_id": NO_ID,
        "turn_id": NO_ID,
        "viz_id": NO_ID,
        "code_task_id": NO_ID,
        # The element of a teacher turn that shows a code task: the walks run no such turn.
        "turn_seq": 0,
        "element_id": "e1",
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
            "/v1/sessions/{session_id}/heartbeat",
            no_key="presence; a closed session is reopened under the key session.resume:{seq}",
            body_key="none",
            body=lambda ids, v: {},
        ),
        # The session leaves the left panel (`session.deleted`): keyed by the session; a repeat is
        # «not found».
        Route(
            "DELETE",
            "/v1/sessions/{session_id}",
            no_key="a deletion from the left panel: keyed by the session; a repeat is «not found»",
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
        # Т-§6.10: running writes nothing, so there is nothing a key would protect.
        Route(
            "POST",
            "/v1/tasks/{task_id}/code/run",
            no_key="Т-§6.10: the open tests are run, no event is written",
            body_key="none",
            body=lambda ids, v: {"code": "def square(x): return x * x"},
            path_ids=_CODE_PATH_IDS,
        ),
        # The editor's draft: kept by task, no event (a repeat keeps the same code).
        Route(
            "PUT",
            "/v1/tasks/{task_id}/code/draft",
            no_key="Т-§6.10: the editor's draft is kept by task, no event is written",
            body_key="none",
            body=lambda ids, v: {"code": ["def square(x): return x * x", "def square(x): pass"][v]},
            path_ids=_CODE_PATH_IDS,
        ),
        Route(
            "POST",
            "/v1/sessions/{session_id}/tasks/{task_id}/code/submit",
            stage=185,
            requires=("session_id",),
            body=lambda ids, v: {"code": ["return x * x", "return x ** 2"][v]},
            path_ids=_CODE_PATH_IDS,
            prepare=_code_task,
        ),
        # DEMO-ONLY (`domains/demo_seed.py`): ids and event keys come from the topic names, not
        # from the key of the request.
        Route(
            "POST",
            "/v1/domains/{domain_id}/demo-seed",
            stage=135,
            restore=("domain_id",),
            body=lambda ids, v: {"text": ["sets: union, subset, power", "graphs"][v]},
            no_other_body="idempotent by content: another text adds the topics it names",
        ),
    ),
    other_routes=frozenset(
        {
            ("GET", "/v1/sessions"),
            ("GET", "/v1/sessions/active"),
            ("GET", "/v1/sessions/{session_id}"),
            ("GET", "/v1/sessions/{session_id}/events"),
            ("GET", "/v1/sessions/{session_id}/jobs"),
            ("GET", "/v1/sessions/{session_id}/plan"),
            ("GET", "/v1/llm/access"),
            ("GET", "/v1/viz/{viz_id}"),
            # Т-§6.10: what the code editor reads.
            ("GET", "/v1/tasks/{task_id}/code"),
            ("GET", "/v1/sessions/{session_id}/turns/{turn_seq}/elements/{element_id}/code"),
        }
    ),
    routers=(
        "quack.session.router:router",
        "quack.session.code_routes:router",
        "quack.artifacts.viz_routes:router",
    ),
)
