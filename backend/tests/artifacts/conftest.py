"""Fixtures of Stream 3 tests (ТЗ §5 п.2–3).

The router is mounted locally on `create_app(settings, connect=False)` (ТЗ §0.1).
`httpx.ASGITransport` does not run the lifespan, so the shared test pools are attached to
`app.state.resources` by hand and are not closed here. The LLM provider and the compute
service are in-memory doubles behind the real `LLMGateway` and `ComputeClient`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import jwt
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import AppResources, create_app
from quack.artifacts.deps import WorkerDeps, get_compute_client, get_llm_gateway
from quack.artifacts.router import router
from quack.compute_client.client import ComputeClient
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.events import NewEvent, append_events
from quack.graph.neo4j_store import read_applied_seq
from quack.graph.worker import ProjectorWorker
from quack.llm.gateway import LLMGateway
from quack.llm.provider import ProviderRequest, ProviderResponse
from tests.factories import new_event

# ТЗ Потока 1 §2.1 п.2: HS256; with an empty `jwt_secret` in dev/test this constant signs.
DEV_JWT_SECRET = "quack-dev-jwt-secret"
TOKEN_TTL = timedelta(days=7)

type AuthHeaders = Callable[[UUID], dict[str, str]]


# --- LLM provider and compute doubles -------------------------------------------------


class FakeProvider:
    """`LLMProvider`: the answer is chosen by `request.schema_name` (`TaskGenOutput`,
    `EvaluatorOutput`, `ArtifactGenOutput`); every request is recorded."""

    def __init__(self) -> None:
        self.responses: dict[str, str | Exception] = {}
        self.calls: list[ProviderRequest] = []

    def respond(self, schema_name: str, response: BaseModel | Exception) -> None:
        self.responses[schema_name] = (
            response if isinstance(response, Exception) else response.model_dump_json()
        )

    def calls_for(self, schema_name: str) -> list[ProviderRequest]:
        return [c for c in self.calls if c.schema_name == schema_name]

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        item = self.responses.get(request.schema_name)
        if item is None:
            raise AssertionError(f"unexpected LLM call for schema {request.schema_name!r}")
        if isinstance(item, Exception):
            raise item
        return ProviderResponse(
            content=item, input_tokens=100, output_tokens=20, cached_tokens=0, model=request.model
        )


class FakeCompute(ComputeClient):
    """The real `ComputeClient` over `httpx.MockTransport`: the test sets the next
    `MathCheckResponse` (as a dict); every request body is recorded."""

    def __init__(self) -> None:
        self.response: dict[str, Any] | None = None
        self.requests: list[dict[str, Any]] = []
        transport = httpx.MockTransport(self._handle)
        super().__init__("http://compute", client=httpx.AsyncClient(transport=transport))

    def respond(self, response: dict[str, Any]) -> None:
        self.response = response

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if self.response is None:
            raise AssertionError("unexpected compute call")
        return httpx.Response(200, json=self.response)


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
async def fake_compute() -> AsyncIterator[FakeCompute]:
    compute = FakeCompute()
    yield compute
    await compute.aclose()


# --- Application ----------------------------------------------------------------------


@pytest.fixture
def app(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    fake_provider: FakeProvider,
    fake_compute: FakeCompute,
) -> FastAPI:
    application = create_app(settings, connect=False)
    application.include_router(router)
    application.state.resources = AppResources(
        engine=pg_engine, session_factory=db, neo4j=neo4j, redis=redis
    )
    application.dependency_overrides[get_llm_gateway] = lambda: LLMGateway(
        db, fake_provider, settings
    )
    application.dependency_overrides[get_compute_client] = lambda: fake_compute
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def auth() -> AuthHeaders:
    """`auth(learner_id)` → `Authorization: Bearer <jwt>` (ТЗ Потока 1 §2.1 п.2)."""

    def headers(learner_id: UUID) -> dict[str, str]:
        now = datetime.now(UTC)
        claims = {
            "sub": str(learner_id),
            "iat": int(now.timestamp()),
            "exp": int((now + TOKEN_TTL).timestamp()),
        }
        token = jwt.encode(claims, DEV_JWT_SECRET, algorithm="HS256")
        return {"Authorization": f"Bearer {token}"}

    return headers


@pytest.fixture
def worker_deps(
    settings: Settings,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
    fake_provider: FakeProvider,
) -> WorkerDeps:
    return WorkerDeps(settings, db, LLMGateway(db, fake_provider, settings), neo4j, redis)


# --- Seeding through the real append_events -------------------------------------------


@dataclass(frozen=True)
class Structure:
    """One confirmed domain: topic "Derivatives" with skills A and B."""

    domain_id: UUID
    topic_id: UUID
    skill_a: UUID
    skill_b: UUID
    misconception_a: UUID
    domain_name: str = "Calculus I"
    topic_name: str = "Derivatives"
    skill_a_name: str = "Chain rule"
    skill_b_name: str = "Product rule"
    misconception_a_name: str = "Forgets the inner derivative"


@dataclass
class Seeder:
    settings: Settings
    pg_engine: AsyncEngine
    db: SessionFactory
    neo4j: AsyncDriver

    async def append(self, learner: UUID, *events: NewEvent) -> list[int]:
        async with self.db() as session, session.begin():
            stored = await append_events(session, learner, list(events), False)
        return [e.seq for e in stored]

    async def learner(self) -> UUID:
        """auth.users row + `learner.registered` (ТЗ Потока 1 §2.1 п.1–2)."""
        learner = uuid4()
        email = f"learner-{learner.hex[:12]}@example.com"
        async with self.db() as session, session.begin():
            await session.execute(
                text(
                    "INSERT INTO auth.users (id, email, password_hash, display_name) "
                    "VALUES (:id, :email, 'not-a-real-hash', 'Learner')"
                ),
                {"id": learner, "email": email},
            )
        await self.append(
            learner,
            new_event(
                "learner.registered",
                {"email": email, "display_name": "Learner", "preferences": {}},
                actor="learner",
                key=f"register:{learner}",
            ),
        )
        return learner

    async def seed_structure(self, learner: UUID) -> Structure:
        """`domain.created`, `structure.proposed` (1 topic, skills A and B),
        `structure.confirmed`."""
        s = Structure(uuid4(), uuid4(), uuid4(), uuid4(), uuid4())
        d, t = str(s.domain_id), str(s.topic_id)
        await self.append(
            learner,
            new_event(
                "domain.created",
                {"domain_id": d, "name": s.domain_name, "slug": f"calculus-{d[:8]}"},
                actor="learner",
            ),
            new_event(
                "structure.proposed",
                {
                    "domain_id": d,
                    "structure_version": 1,
                    "topics": [{"topic_id": t, "name": s.topic_name}],
                    "skills": [
                        {"skill_id": str(s.skill_a), "topic_id": t, "name": s.skill_a_name},
                        {"skill_id": str(s.skill_b), "topic_id": t, "name": s.skill_b_name},
                    ],
                    "prereqs": [],
                    "skill_deps": [],
                    "misconceptions": [
                        {
                            "misconception_id": str(s.misconception_a),
                            "skill_id": str(s.skill_a),
                            "name": s.misconception_a_name,
                        }
                    ],
                },
                actor="structurer@1",
            ),
            new_event(
                "structure.confirmed", {"domain_id": d, "structure_version": 1}, actor="learner"
            ),
        )
        return s

    async def start_session(self, learner: UUID, domain_id: UUID, topic_id: UUID) -> UUID:
        sid = uuid4()
        await self.append(
            learner,
            new_event(
                "session.started",
                {
                    "session_id": str(sid),
                    "domain_id": str(domain_id),
                    "topic_id": str(topic_id),
                    "mode": "practice",
                    "goal": None,
                    "plan": [],
                },
            ),
        )
        return sid

    async def complete_session(self, learner: UUID, session_id: UUID, domain_id: UUID) -> None:
        """`session.completed`: only one active session per learner (uq_sessions_one_active)."""
        await self.append(
            learner,
            new_event(
                "session.completed",
                {"session_id": str(session_id), "domain_id": str(domain_id), "summary_stats": {}},
            ),
        )

    async def reply(
        self, learner: UUID, session_id: UUID, turn_index: int, text_: str, *, teacher: bool
    ) -> None:
        payload: dict[str, Any] = {
            "session_id": str(session_id),
            "turn_id": str(uuid4()),
            "turn_index": turn_index,
            "text": text_,
        }
        if teacher:
            payload["pedagogical_move"] = "explain"
            event = new_event("turn.teacher_replied", payload, actor="teacher@1")
        else:
            event = new_event("turn.student_replied", payload, actor="learner")
        await self.append(learner, event)

    async def seed_bank_task(
        self,
        learner: UUID,
        s: Structure,
        *,
        skill_id: UUID,
        family: str,
        kind: str,
        reference_solution: dict[str, Any],
        prompt_text: str = "Solve the task.",
        difficulty: float = 0.5,
    ) -> UUID:
        """`task.generated` → a row of core.task_bank."""
        task_id = uuid4()
        await self.append(
            learner,
            new_event(
                "task.generated",
                {
                    "task_id": str(task_id),
                    "domain_id": str(s.domain_id),
                    "topic_id": str(s.topic_id),
                    "family_id": family,
                    "difficulty": difficulty,
                    "kind": kind,
                    "prompt_text": prompt_text,
                    "reference_solution": reference_solution,
                    "target_skills": [{"skill": {"skill_id": str(skill_id)}, "weight": 1.0}],
                },
                actor="task_gen@1",
            ),
        )
        return task_id

    async def assign_task(
        self, learner: UUID, session_id: UUID, task_id: UUID, topic_id: UUID
    ) -> None:
        await self.append(learner, _assigned(session_id, task_id, topic_id))

    async def solve_task(
        self, learner: UUID, session_id: UUID, task_id: UUID, topic_id: UUID, skill_id: UUID
    ) -> None:
        """`task.assigned` + `task.evaluated` (`evaluator="compute"`, correct)."""
        await self.append(
            learner,
            _assigned(session_id, task_id, topic_id),
            new_event(
                "task.evaluated",
                {
                    "session_id": str(session_id),
                    "turn_id": str(uuid4()),
                    "task_id": str(task_id),
                    "evaluator": "compute",
                    "is_correct": True,
                    "score": 1.0,
                    "independence": 1.0,
                    "feedback": "Correct.",
                    "skill_scores": [
                        {"skill_id": str(skill_id), "score": 1.0, "tested_depth": 0.5}
                    ],
                },
                actor="code",
            ),
        )

    async def project(self, learner: UUID) -> None:
        """Run the projector into Neo4j until the learner's whole log is applied."""
        worker = ProjectorWorker(self.settings, self.pg_engine, self.db, self.neo4j, None)
        for _ in range(50):
            outcome = await worker.process_learner(learner)
            assert outcome is not None
            if outcome.status != "applied":
                break
        async with self.db() as session:
            last_seq = (
                await session.execute(
                    text("SELECT last_seq FROM core.learner_seq WHERE learner_id = :l"),
                    {"l": learner},
                )
            ).scalar_one()
        assert await read_applied_seq(self.neo4j, str(learner)) == last_seq


def _assigned(session_id: UUID, task_id: UUID, topic_id: UUID) -> NewEvent:
    return new_event(
        "task.assigned",
        {
            "session_id": str(session_id),
            "turn_id": str(uuid4()),
            "task_id": str(task_id),
            "topic_id": str(topic_id),
        },
    )


@pytest.fixture
def seed(
    settings: Settings, pg_engine: AsyncEngine, db: SessionFactory, neo4j: AsyncDriver
) -> Seeder:
    return Seeder(settings, pg_engine, db, neo4j)
