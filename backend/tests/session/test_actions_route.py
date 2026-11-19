"""ТЗ Ф4 §4.7, WP13.1, WP13.2 over HTTP: `POST /v1/sessions/{id}/actions` — the action is
written, a kind with a teacher turn answers SSE frames in the order of §4.7 (`action_recorded`
first), a kind without a turn answers `201 {seq}`; a closed session comes back (only the sweep
closes one, an action refreshes its activity); an error before the write is an `error` frame and
no turn in the log (`Т-§12.1`).

The context of the turn (WP14) is the port `TurnContextSource`; here a fixed one over the
seam S13 provider (D-B-156)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from redis.asyncio import Redis

from quack.api.app import create_app
from quack.api.auth import SESSION_COOKIE
from quack.auth.sessions import open_session
from quack.config import Settings
from quack.db.db import Db
from quack.events.event_types import DomainCreated, SessionEnded, VoiceTranscribed
from quack.events.log import NewEvent, append_events
from quack.llm.labels import LabelMap
from quack.llm.provider import ProviderRequest
from quack.llm.streaming import StreamRequest
from quack.session.action_turn import TurnRequest, TurnSetup
from quack.session.router import get_turn_context_source
from quack.session.sessions import StartSession, end_if_inactive, get_session, start_session
from quack.session.spoiler_guard import spoiler_hits
from quack.session.turn_commit import TurnManifestRow
from quack.session.turn_graph import AssembledTurn, GuardInputs
from tests.session.helpers import parse_sse
from tests.support.llm import ScriptedFailure, ScriptedLLMProvider

pytest_plugins = ["tests.session.fixtures"]

SCHEMA = "TeacherOutputV2"
TEXT = "Every term is a third of the one before it."
SKILL = uuid4()


def _turn() -> dict[str, Any]:
    return {
        "p1_head": {
            "intent": "explain",
            "targetSkills": ["S1"],
            "verdict_stance": None,
            "branchType": None,
            "topicLabel": None,
            "approach": "example_first",
        },
        "p2_body": {"blocks": [{"block_id": "b1", "text": TEXT, "element_id": None}]},
        "p3_markup": {
            "tasks": [],
            "elements": [],
            "exposures": [{"skill": "S1", "kind": "explained", "approach": "example_first"}],
            "targetsMisconception": None,
            "planUpdate": {
                "goal": None,
                "current_step": {"id": "geometric-sum", "text": "geometric sum", "landmark": None},
                "next_steps": [],
                "landmark_edits": None,
                "branches": [],
                "cancel_requests": [],
            },
            "agentRequests": [],
            "correctionsAcknowledged": [],
            "decisionReason": "explain first",
            "deadline": None,
        },
    }


@dataclass
class FixedSource:
    """`TurnContextSource` over the S13 provider: the request, the label table, the manifest."""

    settings: Settings
    provider: ScriptedLLMProvider = field(default_factory=ScriptedLLMProvider)
    asked: list[TurnRequest] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.provider.respond(SCHEMA, lambda _r: _turn())

    async def assemble(self, request: TurnRequest) -> TurnSetup:
        self.asked.append(request)
        labels = LabelMap.build(self.settings, skills=[SKILL])
        base = ProviderRequest(
            model="muse-wonder-1.0",
            prompt="context",
            json_schema={"type": "object"},
            schema_name=SCHEMA,
            temperature=0.0,
            timeout_sec=30.0,
            reasoning_effort="low",
            max_output_tokens=8192,
        )

        async def no_tools(name: str, arguments: str) -> str:
            return f"no tool {name}"

        return TurnSetup(
            assembled=AssembledTurn(
                request=StreamRequest(base),
                guard=GuardInputs(
                    answered_verdict=None,
                    skill_labels=frozenset({"S1"}),
                    misconception_labels=frozenset(),
                    task_labels=frozenset(),
                    has_spoiler=lambda _t: False,
                    remove_spoiler=lambda t: t,
                ),
                effort="low",
                degraded_mode=None,
            ),
            provider=self.provider,
            labels=labels,
            manifest=TurnManifestRow(
                manifest_id=request.manifest_id,
                learner_id=request.learner_id,
                session_id=request.session_id,
                prompt_version="teacher@2",
                doctrine_version=None,
                renderer_version="renderer@1",
                role=request.role,
                model="muse-wonder-1.0",
                effort="low",
                projected_seq=0,
                label_table=labels.table(),
            ),
            execute_tool=no_tools,
        )


@pytest.fixture
async def app(
    settings: Settings,
    neo4j: AsyncDriver,
    redis: Redis,
    pg_db: Db,
) -> FastAPI:
    from tests.support.resources import build_app_resources

    resources = build_app_resources(settings, neo4j, redis, db=pg_db)
    return create_app(settings, resources=resources)


@pytest.fixture
def source(app: FastAPI, settings: Settings) -> FixedSource:
    fixed = FixedSource(settings)
    app.dependency_overrides[get_turn_context_source] = lambda: fixed
    return fixed


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """The route takes the learner from the session cookie only (WP18.3, D-C-119)."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@dataclass(frozen=True)
class Learner:
    learner_id: UUID
    session_id: UUID
    cookies: dict[str, str]


async def _learner(pg_db: Db, settings: Settings) -> Learner:
    learner_id, domain_id = uuid4(), uuid4()
    await pg_db.execute(
        "INSERT INTO core.learners (learner_id, email) VALUES (%(l)s, %(e)s)",
        {"l": learner_id, "e": f"{learner_id}@test.invalid"},
    )
    await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type="domain.created",
                payload=DomainCreated(domain_id=domain_id, name="Calculus"),
                actor="learner",
                idempotency_key=f"domain:{domain_id}",
            )
        ],
    )
    started = await start_session(
        pg_db,
        learner_id=learner_id,
        body=StartSession(domain_id=domain_id, intent="learn", idempotency_key=uuid4()),
    )
    token = await open_session(pg_db, settings, learner_id=learner_id, now=datetime.now(UTC))
    return Learner(learner_id, started.session_id, {SESSION_COOKIE: token})


async def _post(
    client: httpx.AsyncClient, who: Learner, body: dict[str, Any], session_id: UUID | None = None
) -> httpx.Response:
    return await client.post(
        f"/v1/sessions/{session_id or who.session_id}/actions",
        json={"idempotency_key": str(uuid4()), **body},
        cookies=who.cookies,
    )


async def _types(pg_db: Db, learner_id: UUID) -> list[str]:
    rows = await pg_db.fetch_all(
        "SELECT type FROM core.events WHERE learner_id = %(l)s ORDER BY seq", {"l": learner_id}
    )
    return [r["type"] for r in rows]


def _names(frames: list[tuple[str, dict[str, Any]]]) -> list[str]:
    names: list[str] = []
    for name, _ in frames:
        if not (name == "token" and names and names[-1] == "token"):
            names.append(name)
    return names


async def test_message_streams_the_turn_frames(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    who = await _learner(pg_db, settings)

    response = await _post(client, who, {"kind": "message", "text": "why does it converge?"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = parse_sse(response.text)
    assert _names(frames) == ["action_recorded", "status", "token", "turn_committed"]
    recorded, committed = frames[0][1], frames[-1][1]
    assert "".join(d["text"] for n, d in frames if n == "token") == TEXT
    assert committed["role"] == "teacher" and committed["effort"] == "low"
    types = await _types(pg_db, who.learner_id)
    assert types[-2:] == ["message.sent", "turn.committed"]
    turn = await pg_db.fetch_one(
        "SELECT seq, payload FROM core.events WHERE learner_id = %(l)s AND type = 'turn.committed'",
        {"l": who.learner_id},
    )
    assert turn is not None and turn["seq"] == committed["seq"]
    assert turn["payload"]["action_seq"] == recorded["seq"]
    [asked] = source.asked
    assert (asked.session_id, asked.action_seq, asked.role) == (
        who.session_id,
        recorded["seq"],
        "teacher",
    )
    manifest = await pg_db.fetch_one(
        "SELECT 1 AS found FROM ops.turn_manifests WHERE learner_id = %(l)s",
        {"l": who.learner_id},
    )
    assert manifest is not None


async def test_action_without_a_turn_answers_201(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    who = await _learner(pg_db, settings)

    response = await _post(client, who, {"kind": "confidence", "target_seq": 1, "value": 3})

    assert response.status_code == 201, response.text
    seq = response.json()["seq"]
    assert (await _types(pg_db, who.learner_id))[-1] == "confidence.reported"
    assert isinstance(seq, int) and source.asked == []


async def test_session_of_another_learner_is_not_found(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    who, other = await _learner(pg_db, settings), await _learner(pg_db, settings)

    response = await _post(client, who, {"kind": "message", "text": "hi"}, other.session_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "message.sent" not in await _types(pg_db, who.learner_id)


async def test_without_a_session_cookie_is_unauthenticated(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    who = await _learner(pg_db, settings)

    response = await client.post(
        f"/v1/sessions/{who.session_id}/actions",
        json={"kind": "message", "text": "hi", "idempotency_key": str(uuid4())},
        cookies={SESSION_COOKIE: "not-a-session"},  # only a live session signs in (WP18.3)
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


async def test_companion_session_is_forbidden(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """T-CP1: the phone's `companion` session reaches no other endpoint — `403 UNAUTHORIZED`
    (D-C-128), no action is written, no turn runs."""
    who = await _learner(pg_db, settings)
    phone = await open_session(
        pg_db,
        settings,
        learner_id=who.learner_id,
        now=datetime.now(UTC),
        scope="companion",
        canvas_id=uuid4(),
    )

    response = await client.post(
        f"/v1/sessions/{who.session_id}/actions",
        json={"kind": "message", "text": "hi", "idempotency_key": str(uuid4())},
        cookies={SESSION_COOKIE: phone},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
    assert "message.sent" not in await _types(pg_db, who.learner_id) and source.asked == []


async def test_session_ended_by_the_sweeper_comes_back(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """WP13.10: an action in a session the sweeper ended writes `session.started` again."""
    who = await _learner(pg_db, settings)
    late = datetime.now(UTC) + timedelta(minutes=settings.session_inactivity_timeout_min + 1)
    assert await end_if_inactive(
        pg_db, learner_id=who.learner_id, session_id=who.session_id, settings=settings, now=late
    )

    response = await _post(client, who, {"kind": "message", "text": "back again"})

    assert response.status_code == 200, response.text
    types = await _types(pg_db, who.learner_id)
    assert (
        types[-5:]
        == [
            "session.started",
            "session.ended",
            "session.started",
            "message.sent",
            "turn.committed",
        ][-5:]
    )


async def test_session_closed_with_the_retired_reason_learner_comes_back(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """Any closed session comes back (WP13.10); an old log with `session.ended {reason:
    learner}` is read like the others."""
    who = await _learner(pg_db, settings)
    await pg_db.execute(
        "UPDATE content.sessions SET status = 'ended', ended_at = now() WHERE session_id = %(s)s",
        {"s": who.session_id},
    )
    await append_events(
        pg_db,
        who.learner_id,
        [
            NewEvent(
                type="session.ended",
                payload=SessionEnded(session_id=who.session_id, reason="learner"),
                actor="learner",
                idempotency_key=f"old-end:{who.session_id}",
                session_id=who.session_id,
            )
        ],
    )

    response = await _post(client, who, {"kind": "message", "text": "still here"})

    assert response.status_code == 200, response.text
    assert "message.sent" in await _types(pg_db, who.learner_id)
    row = await get_session(pg_db, learner_id=who.learner_id, session_id=who.session_id)
    assert row.status == "active" and row.ended_at is None


async def test_an_action_refreshes_the_activity_so_the_sweep_keeps_the_session(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """WP13.10: a message counts as activity; a session with a recent message is not closed
    by the sweep even if the last heartbeat is old."""
    who = await _learner(pg_db, settings)
    old = datetime.now(UTC) - timedelta(minutes=settings.session_inactivity_timeout_min - 1)
    await pg_db.execute(
        "UPDATE content.sessions SET last_activity_at = %(old)s WHERE session_id = %(s)s",
        {"old": old, "s": who.session_id},
    )

    response = await _post(client, who, {"kind": "message", "text": "thinking aloud"})

    assert response.status_code == 200, response.text
    row = await get_session(pg_db, learner_id=who.learner_id, session_id=who.session_id)
    assert row.last_activity_at > old + timedelta(minutes=10)
    later = datetime.now(UTC) + timedelta(minutes=settings.session_inactivity_timeout_min - 1)
    assert not await end_if_inactive(
        pg_db, learner_id=who.learner_id, session_id=who.session_id, settings=settings, now=later
    )
    assert (
        await get_session(pg_db, learner_id=who.learner_id, session_id=who.session_id)
    ).status == "active"


async def test_error_before_the_write_is_a_frame_and_no_turn(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """`Т-§12.1`: the provider is down — an `error` frame; the action stays, no turn."""
    source.provider.fail(ScriptedFailure("unavailable", schema_name=SCHEMA, times=10))
    who = await _learner(pg_db, settings)

    response = await _post(client, who, {"kind": "message", "text": "hi"})

    frames = parse_sse(response.text)
    assert _names(frames) == ["action_recorded", "status", "error"]
    error = frames[-1][1]
    assert error["code"] == "LLM_DEGRADED" and error["retryable"] is True
    assert "turn.committed" not in await _types(pg_db, who.learner_id)


async def test_turn_lock_is_released_for_the_next_action(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """The Redis turn lock of a session is freed after the turn: the next action has its turn."""
    who = await _learner(pg_db, settings)

    first = await _post(client, who, {"kind": "message", "text": "one"})
    second = await _post(client, who, {"kind": "message", "text": "two"})

    for response in (first, second):
        assert _names(parse_sse(response.text))[-1] == "turn_committed", response.text
    assert (await _types(pg_db, who.learner_id)).count("turn.committed") == 2


async def _voice(
    pg_db: Db, who: Learner, session_id: UUID | None = None, text: str = "hello"
) -> int:
    [stored] = await append_events(
        pg_db,
        who.learner_id,
        [
            NewEvent(
                type="voice.transcribed",
                payload=VoiceTranscribed(text=text, started_at_ms=0, ended_at_ms=1000),
                actor="learner",
                idempotency_key=f"voice:{uuid4()}",
                session_id=session_id or who.session_id,
            )
        ],
    )
    return stored.seq


async def test_spoken_message_is_one_learner_utterance(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """The words live in `voice.transcribed`; the message that starts the turn points at it."""
    who = await _learner(pg_db, settings)
    seq = await _voice(pg_db, who, text="is it geometric?")

    response = await _post(client, who, {"kind": "message", "voice_seq": seq})

    assert response.status_code == 200, response.text
    sent = await pg_db.fetch_one(
        "SELECT payload FROM core.events WHERE learner_id = %(l)s AND type = 'message.sent'",
        {"l": who.learner_id},
    )
    assert sent is not None
    assert (sent["payload"]["text"], sent["payload"]["voice_seq"]) == ("", seq)
    types = await _types(pg_db, who.learner_id)
    assert types.count("voice.transcribed") == 1 and types.count("message.sent") == 1


@pytest.mark.parametrize("where", ["other_session", "other_learner", "not_voice", "missing"])
async def test_spoken_message_must_point_at_own_voice_of_the_session(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings, where: str
) -> None:
    who, other = await _learner(pg_db, settings), await _learner(pg_db, settings)
    seq = {
        "other_session": lambda: _voice(pg_db, who, session_id=uuid4()),
        "other_learner": lambda: _voice(pg_db, other),
        "not_voice": lambda: _first_seq(pg_db, who),
        "missing": lambda: _no_seq(),
    }[where]
    response = await _post(client, who, {"kind": "message", "voice_seq": await seq()})

    assert response.status_code == 404
    assert "message.sent" not in await _types(pg_db, who.learner_id)


async def _first_seq(pg_db: Db, who: Learner) -> int:
    row = await pg_db.fetch_one(
        "SELECT min(seq) AS seq FROM core.events WHERE learner_id = %(l)s", {"l": who.learner_id}
    )
    assert row is not None
    return int(row["seq"])


async def _no_seq() -> int:
    return 10**9


async def test_spoken_message_with_text_is_rejected(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    who = await _learner(pg_db, settings)
    seq = await _voice(pg_db, who)

    response = await _post(client, who, {"kind": "message", "voice_seq": seq, "text": "again"})

    assert response.status_code == 422
    assert "message.sent" not in await _types(pg_db, who.learner_id)


class SpoilerSource(FixedSource):
    """The same turn context, with the answer `1/3` of an active task guarded."""

    async def assemble(self, request: TurnRequest) -> TurnSetup:
        setup = await super().assemble(request)
        guard = replace(
            setup.assembled.guard,
            has_spoiler=lambda t: "1/3" in t,
            spoiler_hits=lambda t: spoiler_hits(t, ["1/3"], self.settings),
        )
        return replace(setup, assembled=replace(setup.assembled, guard=guard))


async def test_a_shadow_spoiler_hit_is_kept_after_the_turn_and_changes_nothing(
    client: httpx.AsyncClient, app: FastAPI, pg_db: Db, settings: Settings
) -> None:
    """Through the route, in `shadow`: the reply with the answer is streamed and committed whole;
    the hit is in `turn.committed.guard_events` and the matched sentence in `ops.guard_samples`
    (not the whole reply)."""
    spoiled = f"{TEXT} The sum is 1/3. Check it."
    source = SpoilerSource(settings)
    turn = _turn()
    turn["p2_body"]["blocks"][0]["text"] = spoiled
    source.provider.respond(SCHEMA, lambda _r: turn)
    app.dependency_overrides[get_turn_context_source] = lambda: source
    who = await _learner(pg_db, settings)

    response = await _post(client, who, {"kind": "message", "text": "what is the sum?"})

    assert response.status_code == 200, response.text
    frames = parse_sse(response.text)
    assert "reset" not in _names(frames) and len(source.asked) == 1
    assert "".join(d["text"] for n, d in frames if n == "token") == spoiled
    committed = await pg_db.fetch_one(
        "SELECT seq, payload FROM core.events WHERE learner_id = %(l)s AND type = 'turn.committed'",
        {"l": who.learner_id},
    )
    assert committed is not None and committed["payload"]["text"] == spoiled
    assert committed["payload"]["guard_events"] == [
        {"rule": "spoiler", "reason": "answer_in_text", "phase": "spoiler_shadow", "retried": False}
    ]
    [sample] = await pg_db.fetch_all(
        "SELECT * FROM ops.guard_samples WHERE learner_id = %(l)s", {"l": who.learner_id}
    )
    assert sample["session_id"] == who.session_id and sample["turn_seq"] == committed["seq"]
    assert (sample["rule"], sample["reason"]) == ("spoiler", "shadow")
    assert (sample["sentence"], sample["answer_token"]) == ("The sum is 1/3.", "1/3")


async def test_the_route_logs_and_commits_the_turn_timings(
    client: httpx.AsyncClient, app: FastAPI, pg_db: Db, settings: Settings
) -> None:
    """A turn through the route: `turn.committed.timings` and the `turn_timings` log carry the
    same numbers (the log adds the write), no text of the dialogue."""
    from structlog.testing import capture_logs

    source = SpoilerSource(settings)
    source.provider.respond(SCHEMA, lambda _r: _turn())
    app.dependency_overrides[get_turn_context_source] = lambda: source
    who = await _learner(pg_db, settings)

    with capture_logs() as logs:
        response = await _post(client, who, {"kind": "message", "text": "what is the sum?"})

    assert response.status_code == 200, response.text
    committed = await pg_db.fetch_one(
        "SELECT seq, payload FROM core.events WHERE learner_id = %(l)s AND type = 'turn.committed'",
        {"l": who.learner_id},
    )
    assert committed is not None
    timings = committed["payload"]["timings"]
    assert {"slot_wait_ms", "admit_ms", "assemble_total_ms", "ttft_ms", "total_ms"} <= set(timings)
    assert all(isinstance(v, int) and v >= 0 for v in timings.values())
    [entry] = [e for e in logs if e["event"] == "turn_timings"]
    assert entry["turn_seq"] == committed["seq"] and entry["commit_ms"] >= 0
    assert {k: entry[k] for k in timings} == timings
    assert "what is the sum?" not in str(entry)


async def test_element_response_streams_verdict_and_persists_it_for_reload(
    client: httpx.AsyncClient, source: FixedSource, pg_db: Db, settings: Settings
) -> None:
    """`POST /v1/sessions/{id}/actions` with `element_response` (no `answer`) streams the
    instant verdict in `action_recorded` and persists `element.responded`, `answer.submitted`
    and `verdict.recorded` so `GET /v1/sessions/{id}` restores the verdict on reload."""
    from quack.events import records
    from quack.events.values import AnswerSpec, MathValue
    from quack.practice.role_outputs import TaskCondition
    from quack.practice.task_store import create_task

    who = await _learner(pg_db, settings)
    task_id = uuid4()
    spec = AnswerSpec(type="number", value=MathValue(latex="1/2", sympy="1/2"))
    await create_task(
        pg_db,
        learner_id=who.learner_id,
        task_id=task_id,
        family_id=task_id,
        source="teacher",
        condition=TaskCondition(text="1/4 + 1/4?", latex=None),
        markup=records.TaskMarkupRecord(
            tests=[records.TestedLevelRecord(skill_id=SKILL, level=2)],
            method_named=False,
            answer=spec,
        ),
        verification="unchecked",
        actor="teacher@3",
        idempotency_key=f"task:{task_id}",
    )
    markup = records.TaskMarkupRecord(
        tests=[records.TestedLevelRecord(skill_id=SKILL, level=2)],
        method_named=False,
        answer=spec,
    )
    [turn] = await append_events(
        pg_db,
        who.learner_id,
        [
            NewEvent(
                type="turn.committed",
                payload={
                    "action_seq": 1,
                    "text": "Compute 1/4 + 1/4.",
                    "blocks": [{"block_id": "b1", "text": None, "element_id": "e1"}],
                    "head": {
                        "intent": "practice",
                        "target_skill_ids": [str(SKILL)],
                        "verdict_stance": None,
                        "branch_type": None,
                        "approach": "none",
                    },
                    "markup": {
                        "tasks": [
                            {
                                **markup.model_dump(mode="json"),
                                "element_id": "e1",
                                "task_id": str(task_id),
                            }
                        ],
                        "elements": [],
                        "exposures": [],
                        "targets_misconception_id": None,
                        "plan_update": {
                            "goal": None,
                            "current_step": None,
                            "next_steps": None,
                            "landmark_edits": None,
                            "branches": [],
                            "cancel_request_ids": [],
                        },
                        "agent_requests": [],
                        "corrections_acknowledged": [],
                        "decision_reason": "r",
                        "deadline": None,
                    },
                    "role": "teacher",
                    "model": "m",
                    "effort": "low",
                    "degraded_mode": None,
                    "guard_retries": 0,
                    "manifest_id": str(uuid4()),
                },
                actor="teacher@3",
                idempotency_key=f"turn:{uuid4()}",
                session_id=who.session_id,
            )
        ],
    )

    response = await _post(
        client,
        who,
        {"kind": "element_response", "turn_seq": turn.seq, "element_id": "e1", "response": "0.5"},
    )

    assert response.status_code == 200, response.text
    frames = parse_sse(response.text)
    assert frames[0][0] == "action_recorded"
    assert frames[0][1]["verdict"]["outcome"] == "correct"

    detail = (await client.get(f"/v1/sessions/{who.session_id}", cookies=who.cookies)).json()
    responded = next(e for e in detail["events"] if e["type"] == "element.responded")
    verdict = next(e for e in detail["events"] if e["type"] == "verdict.recorded")
    assert responded["turn_seq"] == turn.seq
    assert responded["element_id"] == "e1"
    assert responded["outcome"] == "correct"
    assert verdict["turn_seq"] == turn.seq
    assert verdict["element_id"] == "e1"
    assert verdict["outcome"] == "correct"
