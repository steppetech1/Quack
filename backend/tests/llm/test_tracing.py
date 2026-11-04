"""ТЗ Ф4 WP10.6 (Т-§11.7, Д-§7, Д-§11, D-B-200..D-B-202; T-AI8): a trace of each call of the
gateway with the role, the prompt version and the pseudonym of the learner; a failure of the
tracer does not break the call; the HTTP tracer sends no `learner_id`."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4, uuid5

import httpx
from pydantic import BaseModel, SecretStr

from quack.config import Settings
from quack.db import Db
from quack.llm.gateway import LLMGateway
from quack.llm.provider import ProviderRequest, ProviderResponse
from quack.llm.tracing import (
    LangSmithTracer,
    NullTracer,
    TraceRecord,
    create_tracer,
    learner_pseudonym,
)
from quack.prompts.registry import PromptRegistry, PromptTemplate
from tests.support.tracer import RecordingTracer


class Answer(BaseModel):
    label: str


class _Registry(PromptRegistry):
    def load(self, role: str, version: int) -> PromptTemplate:
        return PromptTemplate(role, version, "Answer.", "0" * 64)


class _Provider:
    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        return ProviderResponse('{"label": "ok"}', 100, 50, 40, request.model)


def _gateway(db: Db, tracer: RecordingTracer) -> LLMGateway:
    return LLMGateway(_Provider(), Settings(env="test"), _Registry(), db=db, tracer=tracer)


async def test_tracing_pseudonym(pg_db: Db) -> None:
    tracer, learner = RecordingTracer(), uuid4()
    gw = _gateway(pg_db, tracer)

    await gw.generate_structured("teacher", 3, Answer, {}, learner, None, None, f"k:{uuid4()}")

    (record,) = tracer.records
    namespace = gw.settings.langsmith_pseudonym_ns
    assert (record.role, record.prompt, record.status) == ("teacher", "teacher@3", "ok")
    assert record.pseudonym == str(uuid5(UUID(namespace), str(learner)))
    assert (record.input_tokens, record.output_tokens, record.cached_tokens) == (100, 50, 40)
    assert str(learner) not in repr(record)


async def test_tracer_failure_does_not_break_the_call(pg_db: Db) -> None:
    gw = _gateway(pg_db, RecordingTracer(fail=True))

    result = await gw.generate_structured(
        "teacher", 1, Answer, {}, uuid4(), None, None, f"k:{uuid4()}"
    )

    assert result.value.label == "ok"


async def test_langsmith_tracer_sends_pseudonym_without_learner_id() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    settings = Settings(env="dev", process_role="api", langsmith_api_key=SecretStr("ls-key"))
    client = httpx.AsyncClient(
        base_url=settings.langsmith_endpoint,
        headers={"x-api-key": "ls-key"},
        transport=httpx.MockTransport(handler),
    )
    tracer = LangSmithTracer(settings, client)
    learner = uuid4()
    pseudonym = learner_pseudonym(settings.langsmith_pseudonym_ns, learner)

    await tracer.trace(
        TraceRecord(
            "teacher", "teacher@3", pseudonym, "m", "ok", None, 1, 2, 0, 0, 120, datetime.now(UTC)
        )
    )
    await asyncio.wait_for(tracer.aclose(), 5)

    (request,) = sent
    body = request.content.decode()
    assert request.url.path == "/runs" and request.headers["x-api-key"] == "ls-key"
    assert json.loads(body)["extra"]["metadata"]["learner"] == pseudonym
    assert str(learner) not in body
    assert await tracer.delete_learner(pseudonym) is False


def test_tracer_is_off_in_tests_and_outside_api_and_workers() -> None:
    key = SecretStr("ls-key")
    assert isinstance(create_tracer(Settings(env="test", langsmith_api_key=key)), NullTracer)
    assert isinstance(
        create_tracer(Settings(env="dev", process_role="projector", langsmith_api_key=key)),
        NullTracer,
    )
    assert isinstance(create_tracer(Settings(env="dev", process_role="api")), NullTracer)
    assert isinstance(
        create_tracer(Settings(env="dev", process_role="workers", langsmith_api_key=key)),
        LangSmithTracer,
    )
