"""The frontier analyst, WP16.1 (ТЗ Ф4; Т-§13.5, Т-§13.2; `T-AG1`): the input is structure only,
a previous digest changes nothing (law 10); labels become ids, an unknown one is dropped; the
digest and the forecast requests in one transaction with keys from the trigger. D-B-176."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import BaseModel

from quack.agents.analyst import (
    ANALYST_ACTOR,
    NONE_LINE,
    AnalystInput,
    digest_key,
    render_evidence,
    run_analyst,
)
from quack.agents.schemas import (
    AnalystOutput,
    FrontierItem,
    GapHypothesis,
    PreparationRequest,
    SuggestedMove,
)
from quack.config import Settings
from quack.db import Db
from quack.events.event_types import ObservationExtracted
from quack.events.log import StoredEvent
from quack.events.records import ExtractedElementRecord
from quack.llm.gateway import LLMGateway, LLMResult
from quack.llm.labels import LabelMap
from quack.prompts.registry import PromptRegistry
from tests.db.fixtures import create_learner

SETTINGS = Settings()
SKILL, OTHER = uuid4(), uuid4()
MISCONCEPTION = uuid4()
DOMAIN, SESSION = uuid4(), uuid4()
LABELS = LabelMap.build(SETTINGS, skills=[SKILL, OTHER], misconceptions=[MISCONCEPTION])


@dataclass
class _Gateway:
    answer: AnalystOutput
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append(
            {
                "role": role,
                "prompt_role": kwargs.get("prompt_role"),
                "variables": variables,
                "key": idempotency_key,
                "check": kwargs.get("check"),
            }
        )
        return LLMResult(
            value=self.answer, from_cache=False, prompt_hash="h", model="m", usage=None
        )


def _element(skill_id: UUID | None, **kw: Any) -> ExtractedElementRecord:
    base: dict[str, Any] = {
        "kind": "evidence",
        "skill_id": skill_id,
        "new_skill": None,
        "skill_unclear": False,
        "level": 3,
        "outcome": "incorrect",
        "help": "hint",
        "hint_step": 1,
        "gap_type": "confusion",
        "misconception_id": None,
        "new_misconception": None,
        "root_cause": None,
        "quote": "the root test",
        "events": [5],
        "at_ms": 1,
    }
    return ExtractedElementRecord.model_validate(base | kw)


def _observation(elements: list[ExtractedElementRecord]) -> StoredEvent:
    payload = ObservationExtracted(
        episode_seq=9, extractor="extractor@3", trigger_seq=9, elements=elements
    )
    return StoredEvent(
        learner_id=uuid4(),
        seq=10,
        id=10,
        type="observation.extracted",
        schema_version=1,
        actor="extractor@3",
        domain_id=DOMAIN,
        session_id=SESSION,
        causation_seq=9,
        correlation_id=None,
        idempotency_key="obs",
        payload_hash="h",
        occurred_at=datetime.now(UTC),
        payload=payload.model_dump(mode="json"),
        source_refs=[],
    )


def _output() -> AnalystOutput:
    return AnalystOutput(
        frontier=[
            FrontierItem(skill="S1", level=3, state="fails when to apply"),
            FrontierItem(skill="S9", level=2, state="unknown label"),
        ],
        gap_hypotheses=[GapHypothesis(skill="S2", gap_type="confusion", reason="mixes tests")],
        uncertainty=None,
        plan_divergence=True,
        moves=[SuggestedMove(move="a", reason="r"), SuggestedMove(move="b", reason="r")],
        forecast=[
            PreparationRequest(
                kind="task_generation",
                skill="S1",
                level=3,
                misconception="M1",
                description="two retests",
            ),
            PreparationRequest(
                kind="viz", skill="S7", level=None, misconception=None, description="dropped"
            ),
        ],
        manner="short steps",
        text="S1 is the frontier.",
    )


def _facts(as_of_seq: int = 20) -> AnalystInput:
    return AnalystInput(
        domain_id=DOMAIN,
        session_id=SESSION,
        as_of_seq=as_of_seq,
        skills_state="S1: applies — failed yesterday",
        misconceptions="M1 (S1): uses the root test for factorials",
        session_plan="",
        goals="",
        profile="",
        labels=LABELS,
    )


def test_evidence_lines() -> None:
    observation = _observation(
        [
            _element(SKILL),
            _element(OTHER, outcome="correct", help="none", gap_type="none", level=None),
            _element(uuid4()),  # a skill without a label is not in the input
            _element(SKILL, kind="misconception_hypothesis"),
        ]
    )
    assert render_evidence([observation], LABELS).splitlines() == [
        'S1 level 3: incorrect, after a hint, gap: confusion; "the root test"',
        'S2: correct, on their own; "the root test"',
    ]
    assert render_evidence([], LABELS) == NONE_LINE


async def test_analyst_no_previous_digest(pg_db: Db) -> None:
    """T-AG1: the input has no digest or manner — a written digest changes nothing; the deep
    analyst is `analyst_deep` on the same prompt; labels become ids, unknown ones are dropped;
    the forecast is `agent.requested` from `agent:analyst`; a retry writes nothing new."""
    learner_id = await create_learner(pg_db)
    gateway = _Gateway(_output())
    observations = [_observation([_element(SKILL)])]

    async def run(trigger_seq: int, *, deep: bool = False) -> list[StoredEvent]:
        return await run_analyst(
            pg_db,
            cast(LLMGateway, gateway),
            SETTINGS,
            learner_id=learner_id,
            trigger_seq=trigger_seq,
            deep=deep,
            facts=_facts(),
            observations=observations,
        )

    digest, request = await run(30)
    first = gateway.calls[0]
    assert set(first["variables"]) == {
        "skills_state",
        "misconceptions",
        "evidence",
        "session_plan",
        "goals",
        "profile",
    }
    PromptRegistry().load("analyst", 1).render(first["variables"])
    assert first["check"](_output()) is not None  # S9, S7 are unknown → back once

    assert digest.type == "digest.written" and digest.actor == "analyst@1"
    assert digest.idempotency_key == digest_key(DOMAIN, 30, "analyst")
    structured = digest.payload["structured"]
    assert [f["skill_id"] for f in structured["frontier"]] == [str(SKILL)]
    assert [g["skill_id"] for g in structured["gap_hypotheses"]] == [str(OTHER)]
    assert digest.payload["manner"] == "short steps"
    assert digest.payload["plan_divergence"] is True
    assert digest.payload["as_of_seq"] == 20
    assert request.type == "agent.requested" and request.actor == ANALYST_ACTOR
    assert request.payload["kind"] == "task_generation"
    assert request.payload["params"]["skill_id"] == str(SKILL)
    assert request.payload["params"]["misconception_id"] == str(MISCONCEPTION)
    assert request.payload["needed_in_session"] is False

    # the digest just written is not an input of the next run (law 10)
    again = await run(31)
    assert gateway.calls[1]["variables"] == first["variables"]
    assert again[0].seq != digest.seq
    assert again[1].payload["request_id"] != request.payload["request_id"]
    # a retry of the same trigger is the same events with the same request id
    retry = await run(30)
    assert [e.seq for e in retry] == [digest.seq, request.seq]
    assert retry[1].payload["request_id"] == request.payload["request_id"]

    deep = await run(30, deep=True)
    assert gateway.calls[-1]["role"] == "analyst_deep"
    assert gateway.calls[-1]["prompt_role"] == "analyst"
    assert deep[0].seq != digest.seq


async def test_analyst_job_reads_memory_for_the_session(pg_db: Db) -> None:
    """The job: the trigger of a session gives domain and session; blocks come from S4 (the
    goals, the profile, the slice); a trigger outside a session writes nothing."""
    from types import SimpleNamespace

    from quack.agents.analyst import analyst_job
    from quack.events.log import NewEvent, append_events
    from quack.jobs.runner import JobContext
    from quack.jobs.types import ClaimedJob
    from tests.support import graph_read as fx

    learner_id = await create_learner(pg_db)
    session_id = uuid4()
    with_session, without = await append_events(
        pg_db,
        learner_id,
        [
            NewEvent(
                type=t,
                payload={"session_id": str(session_id), "reason": "done"},
                actor="learner",
                idempotency_key=f"end:{uuid4()}",
                domain_id=fx.DOMAIN,
                session_id=s,
            )
            for t, s in (("session.ended", session_id), ("session.ended", None))
        ],
    )
    gateway = _Gateway(_output())
    ctx = cast(JobContext, SimpleNamespace(db=pg_db, llm=gateway, settings=SETTINGS))

    def job(seq: int) -> ClaimedJob:
        return ClaimedJob(
            id=uuid4(),
            kind="analyst.run",
            learner_id=learner_id,
            lock_key="a",
            coalesce_key=None,
            priority=0,
            payload={"trigger_seq": seq},
            attempt=1,
            max_attempts=3,
        )

    reader = fx.FixedGraphReader()
    assert await analyst_job(job(without.seq), ctx, reader, learner_id=learner_id, deep=False) == []
    written = await analyst_job(
        job(with_session.seq), ctx, reader, learner_id=learner_id, deep=True
    )
    assert written[0].type == "digest.written" and written[0].session_id == session_id
    variables = gateway.calls[0]["variables"]
    assert 'Hub "fractions exam"' in variables["goals"]
    assert "Preference language: ru" in variables["profile"]
    assert gateway.calls[0]["role"] == "analyst_deep"
