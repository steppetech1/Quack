"""Shared fixtures of the session stream tests (ТЗ §5), loaded as a pytest plugin.

Every test module of `tests/session/` declares `pytest_plugins = ["tests.session.fixtures"]`
(a `conftest.py` here would count as a test-configuration change). The infrastructure
fixtures `settings`, `pg_engine`, `db`, `neo4j`, `redis` come from `tests/conftest.py`.
Plain helpers (`parse_sse`, `make_token`) live in `tests/session/helpers.py`: import them
from there, importing this module directly defeats pytest's assertion rewriting.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import httpx
import orjson
import pytest
from fastapi import FastAPI
from neo4j import AsyncDriver
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from quack.api.app import create_app
from quack.compute_client.client import (
    ComputeClient,
    ComputeUnavailableError,
    MathCheckRequest,
    MathCheckResponse,
)
from quack.config import FoldParams, Settings
from quack.db.postgres import SessionFactory
from quack.events import append_events
from quack.events.models import StoredEvent
from quack.graph.neo4j_store import Neo4jGraphStore
from quack.graph.projector import run_projector_batch
from quack.llm.errors import ProviderUnavailableError
from quack.llm.gateway import LLMGateway
from quack.llm.provider import ProviderRequest, ProviderResponse
from quack.session.deps import get_compute_client, get_llm_gateway
from tests.factories import new_event
from tests.session.helpers import make_token
from tests.support.auth import ensure_users_hook
from tests.support.resources import build_app_resources

# The gateway passes no role to the provider; the response schema identifies it.
SCHEMA_ROLES = {
    "TeacherOutput": "teacher",
    "ExtractorOutput": "extractor",
    "CheckpointerOutput": "checkpointer",
}


# --- app and HTTP client -----------------------------------------------------------------


@pytest.fixture
async def session_app(
    settings: Settings,
    pg_engine: AsyncEngine,
    db: SessionFactory,
    neo4j: AsyncDriver,
    redis: Redis,
) -> FastAPI:
    """`create_app(settings, connect=False)` over the test pools (all routers mounted).

    The lifespan is not run by `ASGITransport`, so the resources set here stay in place
    and the pools are owned (and closed) by the infrastructure fixtures.
    """
    app = create_app(settings, connect=False)
    app.state.resources = build_app_resources(settings, pg_engine, neo4j, redis, session_factory=db)
    return app


@pytest.fixture
async def client(session_app: FastAPI, db: SessionFactory) -> AsyncIterator[httpx.AsyncClient]:
    """Every Bearer subject gets an `auth.users` row before the request (D-C-004)."""
    transport = httpx.ASGITransport(app=session_app)
    hooks = {"request": [ensure_users_hook(db)]}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", event_hooks=hooks
    ) as http:
        yield http


@pytest.fixture
def learner() -> UUID:
    return uuid4()


@pytest.fixture
def auth_headers(settings: Settings, learner: UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(settings, learner)}"}


# --- fake LLM provider -------------------------------------------------------------------

type FakeLLMItem = BaseModel | dict[str, Any] | str | Exception


class FakeLLMProvider:
    """`LLMProvider` answering from per-role queues.

    `push(role, *items)` queues responses (a model, a dict, raw JSON text or an exception
    to raise); `unavailable = True` makes every call raise `ProviderUnavailableError`.
    An empty queue fails the test loudly instead of inventing an answer.
    """

    def __init__(self) -> None:
        self.queues: dict[str, deque[FakeLLMItem]] = defaultdict(deque)
        self.calls: list[tuple[str, ProviderRequest]] = []
        self.unavailable = False
        self.gateway: LLMGateway | None = None

    def push(self, role: str, *items: FakeLLMItem) -> None:
        self.queues[role].extend(items)

    def fail(self, role: str, times: int = 1) -> None:
        self.push(role, *(ProviderUnavailableError("fake provider down") for _ in range(times)))

    def calls_for(self, role: str) -> list[ProviderRequest]:
        return [request for r, request in self.calls if r == role]

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        role = SCHEMA_ROLES.get(request.schema_name, request.schema_name)
        self.calls.append((role, request))
        if self.unavailable:
            raise ProviderUnavailableError("fake provider unavailable")
        queue = self.queues[role]
        if not queue:
            raise AssertionError(f"fake_llm: no queued response for role {role!r}")
        item = queue.popleft()
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            content = item.model_dump_json()
        elif isinstance(item, dict):
            content = orjson.dumps(item).decode()
        else:
            content = item
        return ProviderResponse(
            content=content,
            input_tokens=100,
            output_tokens=20,
            cached_tokens=0,
            model=request.model,
        )


@pytest.fixture
def fake_llm(session_app: FastAPI, db: SessionFactory, settings: Settings) -> FakeLLMProvider:
    """A `FakeLLMProvider` behind a real `LLMGateway` installed into `session_app`."""
    provider = FakeLLMProvider()
    gateway = LLMGateway(db, provider, settings)
    provider.gateway = gateway
    session_app.state.llm_gateway = gateway
    session_app.dependency_overrides[get_llm_gateway] = lambda: gateway
    return provider


# --- fake compute ------------------------------------------------------------------------

type FakeComputeItem = MathCheckResponse | Exception


def math_result(is_equivalent: bool | None, status: str = "completed") -> MathCheckResponse:
    return MathCheckResponse.model_validate(
        {
            "status": status,
            "is_equivalent": is_equivalent,
            "normalized_student": None,
            "diff_hint": None,
            "error_message": None,
        }
    )


class FakeComputeClient(ComputeClient):
    """`ComputeClient` with scripted `check_math` results.

    `push(*items)` queues `MathCheckResponse`s or exceptions; `unavailable = True` (or an
    empty queue) raises `ComputeUnavailableError`, like a dead compute service.
    """

    def __init__(self) -> None:
        super().__init__("http://compute.invalid")
        self.results: deque[FakeComputeItem] = deque()
        self.requests: list[MathCheckRequest] = []
        self.unavailable = False

    def push(self, *items: FakeComputeItem) -> None:
        self.results.extend(items)

    def push_result(self, is_equivalent: bool | None, status: str = "completed") -> None:
        self.push(math_result(is_equivalent, status))

    async def check_math(self, request: MathCheckRequest) -> MathCheckResponse:
        self.requests.append(request)
        if self.unavailable or not self.results:
            raise ComputeUnavailableError("fake compute unavailable")
        item = self.results.popleft()
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
async def fake_compute(session_app: FastAPI) -> AsyncIterator[FakeComputeClient]:
    compute = FakeComputeClient()
    session_app.state.compute_client = compute
    session_app.dependency_overrides[get_compute_client] = lambda: compute
    yield compute
    await compute.aclose()


# --- seeded domain -----------------------------------------------------------------------


@dataclass
class SeededDomain:
    learner_id: UUID
    domain_id: UUID
    topic_id: UUID
    skill_ids: list[UUID]
    misconception_id: UUID
    math_task_ids: list[UUID]
    choice_task_ids: list[UUID]
    events: list[StoredEvent] = field(default_factory=list)

    @property
    def task_ids(self) -> list[UUID]:
        return [*self.math_task_ids, *self.choice_task_ids]

    @property
    def applied_seq(self) -> int:
        return self.events[-1].seq


def _task_generated(
    seed: SeededDomain, task_id: UUID, kind: str, family: str, skill: UUID, **task: Any
) -> dict[str, Any]:
    return {
        "task_id": str(task_id),
        "domain_id": str(seed.domain_id),
        "topic_id": str(seed.topic_id),
        "family_id": family,
        "difficulty": 0.5,
        "kind": kind,
        "target_skills": [{"skill": {"skill_id": str(skill)}, "weight": 1.0}],
        **task,
    }


@pytest.fixture
async def seeded_domain(learner: UUID, db: SessionFactory, neo4j: AsyncDriver) -> SeededDomain:
    """Domain + confirmed structure (1 topic, 2 skills, 1 misconception) + 3 bank tasks.

    Written through `append_events` (which also fills `core.task_bank`) and projected
    into Neo4j with `run_projector_batch`, so `appliedSeq` equals the last seq.
    """
    seed = SeededDomain(
        learner_id=learner,
        domain_id=uuid4(),
        topic_id=uuid4(),
        skill_ids=[uuid4(), uuid4()],
        misconception_id=uuid4(),
        math_task_ids=[uuid4(), uuid4()],
        choice_task_ids=[uuid4()],
    )
    d, t = str(seed.domain_id), str(seed.topic_id)
    power, chain = seed.skill_ids
    math1, math2 = seed.math_task_ids
    (choice,) = seed.choice_task_ids
    events = [
        new_event("domain.created", {"domain_id": d, "name": "Calculus", "slug": "calculus"}),
        new_event(
            "structure.proposed",
            {
                "domain_id": d,
                "structure_version": 1,
                "topics": [{"topic_id": t, "name": "Derivatives"}],
                "skills": [
                    {"skill_id": str(power), "topic_id": t, "name": "Power rule"},
                    {"skill_id": str(chain), "topic_id": t, "name": "Chain rule"},
                ],
                "prereqs": [],
                "skill_deps": [],
                "misconceptions": [
                    {
                        "misconception_id": str(seed.misconception_id),
                        "skill_id": str(power),
                        "name": "Forgets to lower the exponent",
                    }
                ],
            },
        ),
        new_event("structure.confirmed", {"domain_id": d, "structure_version": 1}),
        new_event(
            "task.generated",
            _task_generated(
                seed,
                math1,
                "math",
                "power-rule",
                power,
                prompt_text="Find d/dx x^3",
                reference_solution={"sympy": "3*x**2", "hints": ["a", "b", "c"]},
                target_misconceptions=[str(seed.misconception_id)],
            ),
        ),
        new_event(
            "task.generated",
            _task_generated(
                seed,
                math2,
                "math",
                "chain-rule",
                chain,
                prompt_text="Find d/dx sin(2x)",
                reference_solution={"sympy": "2*cos(2*x)", "hints": ["a", "b", "c"]},
            ),
        ),
        new_event(
            "task.generated",
            _task_generated(
                seed,
                choice,
                "choice",
                "power-rule-choice",
                power,
                prompt_text="d/dx x^2 is: A) x  B) 2x  C) x^2",
                reference_solution={"option": "B", "hints": ["a", "b", "c"]},
            ),
        ),
    ]
    async with db() as session, session.begin():
        seed.events = await append_events(session, learner, events, publish_wakeup=False)
    store = Neo4jGraphStore(neo4j, str(learner))
    outcome = await run_projector_batch(learner, seed.events, store, FoldParams())
    assert outcome.applied_seq == seed.applied_seq
    return seed
