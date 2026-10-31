"""LLM layer of Phase 3 (ТЗ Фазы 3 §8.4)."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import jsonschema
import orjson
import pytest
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from structlog.testing import capture_logs

from loadtest.meta_mock import minimal_instance
from quack.artifacts.schemas import ArtifactGenOutput
from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.db.redis import llm_circuit_key, llm_pressure_key
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.errors import (
    LLMBudgetExceededError,
    LLMRateLimitedError,
    LLMSchemaError,
    LLMUnavailableError,
)
from quack.llm.gateway import LLMGateway
from quack.llm.pressure import pressured_models, set_pressure_flag
from quack.llm.provider import ProviderRequest, ProviderResponse
from quack.llm.responses_client import ResponsesClient
from quack.llm.strict_schema import StrictSchemaError, to_strict_json_schema
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.prompts.build_manifest import write_manifest
from quack.prompts.registry import PromptRegistry, PromptTemplate
from quack.session.schemas import ExtractorOutput, TeacherOutput
from tests.support.broken import BrokenRedis
from tests.support.llm import ScriptedFailure, ScriptedLLMProvider

# Output models of the eight roles of Phase 3 after WP13 (§7.6).
ROLE_OUTPUT_MODELS: tuple[type[BaseModel], ...] = (
    StructurerOutput,
    DiagnosticOutput,
    CuratorOutput,
    TeacherOutput,
    ExtractorOutput,
    TaskGenOutput,
    EvaluatorOutput,
    ArtifactGenOutput,
)


def _walk(node: Any, path: str = "$") -> list[tuple[str, dict[str, Any]]]:
    """All dict nodes of a JSON Schema with their paths."""
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(node, dict):
        found.append((path, node))
        for key, value in node.items():
            found.extend(_walk(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found.extend(_walk(value, f"{path}[{i}]"))
    return found


def _assert_strict(schema: dict[str, Any]) -> None:
    for path, node in _walk(schema):
        assert "$ref" not in node, f"{path}: $ref is not inlined"
        assert "$defs" not in node, f"{path}: $defs is not inlined"
        for option in node.get("anyOf", []):
            assert option.get("type") != "null", f"{path}: optional field as anyOf with null"
        is_object = node.get("type") == "object" or (
            isinstance(node.get("type"), list) and "object" in node["type"]
        )
        if is_object or "properties" in node:
            assert node.get("additionalProperties") is False, f"{path}: additionalProperties"
            properties = set(node.get("properties", {}))
            assert set(node.get("required", [])) == properties, f"{path}: required != properties"


@pytest.mark.parametrize("model", ROLE_OUTPUT_MODELS, ids=lambda m: m.__name__)
def test_strict_schema_preprocessor(model: type[BaseModel]) -> None:
    """T-L9 (structure): every object closed, every property required, no $ref/$defs, no
    anyOf with null, for the output models of all eight roles."""
    _assert_strict(to_strict_json_schema(model))


def test_strict_schema_optional_field_is_type_with_null() -> None:
    """T-L9: an optional field is a type with `null`, still listed in `required`."""

    class Optional_(BaseModel):
        note: str | None = None
        count: int

    schema = to_strict_json_schema(Optional_)
    assert schema["properties"]["note"]["type"] == ["string", "null"]
    assert set(schema["required"]) == {"note", "count"}
    assert schema["additionalProperties"] is False


def test_strict_schema_inlines_nested_models() -> None:
    """T-L9: nested models are inlined in place of `$ref`."""

    class Inner(BaseModel):
        value: int

    class Outer(BaseModel):
        items: list[Inner]
        single: Inner | None = None

    schema = to_strict_json_schema(Outer)
    _assert_strict(schema)
    assert schema["properties"]["items"]["items"]["properties"]["value"]["type"] == "integer"
    assert schema["properties"]["single"]["type"] == ["object", "null"]


def test_strict_schema_rejects_free_dict_with_field_name() -> None:
    """T-L9: a model with a free dict is a preprocessor error naming the field."""

    class FreeDict(BaseModel):
        title: str
        payload: dict[str, Any]

    with pytest.raises(StrictSchemaError) as exc:
        to_strict_json_schema(FreeDict)
    assert "payload" in exc.value.field_path


async def test_pressured_models_reads_flags(redis: Redis) -> None:
    """Seam S3 (WP5.3): the runner sees exactly the models with a pressure flag; without
    Redis or on a Redis error no model counts as pressured."""
    flagged, other = f"model-{uuid4().hex}", f"model-{uuid4().hex}"
    await redis.set(llm_pressure_key(flagged), "429", ex=30)
    try:
        models = await pressured_models(redis)
        assert flagged in models
        assert other not in models
    finally:
        await redis.delete(llm_pressure_key(flagged))
    assert flagged not in await pressured_models(redis)
    assert await pressured_models(None) == frozenset()
    assert await pressured_models(BrokenRedis()) == frozenset()  # type: ignore[arg-type]  # stand-in client


# --- WP5.1–5.6: gateway reliability (ТЗ Фазы 3 §8.4) -----------------------------------

SPARK, WONDER = "m-spark", "m-wonder"


class GatewayAnswer(BaseModel):
    label: str


@pytest.fixture
def role_prompts(tmp_path: Path) -> PromptRegistry:
    for role in ("extractor", "teacher", "artifacts"):
        folder = tmp_path / role
        folder.mkdir()
        (folder / "v1.md").write_text(f"{role}: {{{{ text }}}}", encoding="utf-8")
    write_manifest(tmp_path)
    return PromptRegistry(tmp_path)


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "env": "test",
        "llm_default_model": SPARK,
        "llm_role_models": {"teacher": WONDER, "teacher_fallback": SPARK},
        "llm_retry_base_ms": 1,
        "llm_rate_limit_base_ms": 1,
        "llm_rate_limit_max_wait_s": 0.5,
    }
    return Settings(**(base | over))


def _gw(
    db: SessionFactory,
    provider: Any,
    prompts: PromptRegistry,
    redis: Redis | None = None,
    **over: Any,
) -> LLMGateway:
    return LLMGateway(db, provider, _settings(**over), prompts, redis=redis)


async def _ask(
    gw: LLMGateway, role: str = "extractor", *, key: str | None = None, **kw: Any
) -> Any:
    return await gw.generate_structured(
        role=role,
        prompt_version=1,
        response_model=GatewayAnswer,
        variables={"text": "x"},
        learner_id=kw.pop("learner_id", uuid4()),
        session_id=kw.pop("session_id", None),
        turn_id=None,
        idempotency_key=key or f"k:{uuid4()}",
        **kw,
    )


def _answer(_request: ProviderRequest) -> dict[str, str]:
    return {"label": "ok"}


def _scripted() -> ScriptedLLMProvider:
    provider = ScriptedLLMProvider()
    provider.respond("GatewayAnswer", _answer)
    return provider


async def _rows(db: SessionFactory, sql: str, **params: Any) -> list[Any]:
    async with db() as session:
        return list((await session.execute(text(sql), params)).all())


class TimedProvider:
    """Provider with a delay that records `(model, start, end)` of every call (T-L1)."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.spans: list[tuple[str, float, float]] = []

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        start = time.monotonic()
        await asyncio.sleep(self.delay)
        self.spans.append((request.model, start, time.monotonic()))
        return ProviderResponse('{"label": "ok"}', 10, 5, 0, request.model)


async def test_semaphores_are_per_model_and_role(
    db: SessionFactory, role_prompts: PromptRegistry
) -> None:
    """T-L1."""
    limits = {"workers": {SPARK: 1, WONDER: 1}, "api": {SPARK: 3, WONDER: 1}}
    provider = TimedProvider(0.1)
    gw = _gw(db, provider, role_prompts, process_role="workers", llm_concurrency=limits)
    await asyncio.gather(_ask(gw), _ask(gw), _ask(gw, "teacher"))
    spark = sorted((s, e) for m, s, e in provider.spans if m == SPARK)
    [(w_start, w_end)] = [(s, e) for m, s, e in provider.spans if m == WONDER]
    assert spark[1][0] >= spark[0][1]  # the two spark calls ran one after the other
    assert w_start < spark[0][1] and spark[0][0] < w_end  # wonder ran next to them

    api = TimedProvider(0.1)
    gw_api = _gw(db, api, role_prompts, process_role="api", llm_concurrency=limits)
    await asyncio.gather(*(_ask(gw_api) for _ in range(3)))
    starts = sorted(s for _, s, _ in api.spans)
    ends = sorted(e for _, _, e in api.spans)
    assert starts[-1] < ends[0]  # api: three spark calls at once


async def test_retry_on_unavailable_then_success(
    db: SessionFactory, role_prompts: PromptRegistry
) -> None:
    """T-L2."""
    provider = _scripted()
    provider.fail(ScriptedFailure("unavailable", times=2))
    learner, key = uuid4(), f"k:{uuid4()}"
    result = await _ask(_gw(db, provider, role_prompts), key=key, learner_id=learner)
    assert result.value.label == "ok"
    ledger = await _rows(
        db, "SELECT status FROM ops.llm_ledger WHERE learner_id = :l ORDER BY created_at", l=learner
    )
    assert sorted(r.status for r in ledger) == ["error", "error", "ok"]
    [outbox] = await _rows(
        db, "SELECT status FROM core.llm_outbox WHERE idempotency_key = :k", k=key
    )
    assert outbox.status == "completed"


async def test_retries_exhausted(db: SessionFactory, role_prompts: PromptRegistry) -> None:
    """T-L3."""
    provider = _scripted()
    provider.fail(ScriptedFailure("unavailable", times=3))
    key = f"k:{uuid4()}"
    with pytest.raises(LLMUnavailableError):
        await _ask(_gw(db, provider, role_prompts), key=key)
    assert len(provider.calls) == 3
    [outbox] = await _rows(
        db, "SELECT status FROM core.llm_outbox WHERE idempotency_key = :k", k=key
    )
    assert outbox.status == "failed"


async def _ttl(redis: Redis, model: str) -> float:
    return await redis.pttl(llm_pressure_key(model)) / 1000


async def test_429_sets_pressure_flag_and_retries(
    db: SessionFactory, redis: Redis, role_prompts: PromptRegistry
) -> None:
    """T-L4 (а)–(д)."""
    model = f"m-{uuid4().hex}"
    over: dict[str, Any] = {"llm_default_model": model, "pressure_default_ttl_s": 3}

    async def flagged(retry_after: str | None) -> float:
        await redis.delete(llm_pressure_key(model))
        provider = _scripted()
        provider.fail(ScriptedFailure("rate_limited", retry_after=retry_after))
        with contextlib.suppress(LLMRateLimitedError):
            await _ask(_gw(db, provider, role_prompts, redis, **over))
        return await _ttl(redis, model)

    # (а) seconds, HTTP date in 5 s, no header.
    assert 0 < await flagged("7") <= 7
    in_5s = format_datetime(datetime.now(UTC) + timedelta(seconds=5), usegmt=True)
    assert 0 < await flagged(in_5s) <= 5
    assert 0 < await flagged(None) <= 3

    # (б) 429 ×3 then success: 4 calls, pauses inside growing windows; 429 ×4 → error.
    provider = _scripted()
    provider.fail(ScriptedFailure("rate_limited", times=3))
    gw = _gw(db, provider, role_prompts, redis, **over, llm_rate_limit_base_ms=20)
    pauses: list[float] = []
    gw.sleep = _recording_sleep(pauses)
    assert (await _ask(gw)).value.label == "ok"
    assert len(provider.calls) == 4
    for k, pause in enumerate(pauses, start=1):
        assert 0.5 * 0.02 * 2 ** (k - 1) <= pause <= 1.5 * 0.02 * 2 ** (k - 1)
    provider = _scripted()
    provider.fail(ScriptedFailure("rate_limited", times=4))
    with pytest.raises(LLMRateLimitedError):
        await _ask(_gw(db, provider, role_prompts, redis, **over))
    assert len(provider.calls) == 4

    # (в) retry-after above llm_rate_limit_max_wait_s: no wait, 1 call, flag set.
    await redis.delete(llm_pressure_key(model))
    provider = _scripted()
    provider.fail(ScriptedFailure("rate_limited", retry_after="60"))
    started = time.monotonic()
    with pytest.raises(LLMRateLimitedError):
        await _ask(_gw(db, provider, role_prompts, redis, **over))
    assert time.monotonic() - started < 0.5
    assert len(provider.calls) == 1
    assert await _ttl(redis, model) > 0

    # (г) hot-path roles never retry a 429.
    for role in ("teacher", "teacher_fallback"):
        provider = _scripted()
        provider.fail(ScriptedFailure("rate_limited", times=3))
        with pytest.raises(LLMRateLimitedError), capture_logs() as logs:
            await _ask(
                _gw(db, provider, role_prompts, redis, **over),
                role,
                prompt_role="teacher" if role == "teacher_fallback" else None,
            )
        assert len(provider.calls) == 1

        # (д) the 429 is logged whole, without the request text.
        [entry] = [e for e in logs if e["event"] == "llm_rate_limited"]
        assert entry["status"] == 429
        assert "retry_after" in entry and "ratelimit" in entry and "body" in entry
        assert "teacher: x" not in repr(entry)


def _recording_sleep(pauses: list[float]) -> Any:
    async def sleep(delay: float) -> None:
        pauses.append(delay)

    return sleep


async def test_circuit_breaker_opens_short_circuits_and_probes(
    db: SessionFactory, redis: Redis, role_prompts: PromptRegistry
) -> None:
    """T-L5."""
    model = f"m-{uuid4().hex}"
    over: dict[str, Any] = {
        "llm_default_model": model,
        "circuit_failures": 5,
        "circuit_open_s": 0.5,
        "llm_retry_attempts": 0,
    }
    provider = _scripted()
    gw = _gw(db, provider, role_prompts, redis, **over)
    provider.fail(ScriptedFailure("unavailable", times=5))
    for _ in range(5):
        with pytest.raises(LLMUnavailableError):
            await _ask(gw)
    assert await redis.exists(llm_circuit_key(model))
    with pytest.raises(LLMUnavailableError) as exc:
        await _ask(gw)
    assert exc.value.reason == "circuit_open"
    assert len(provider.calls) == 5

    await asyncio.sleep(0.6)
    gate = provider.hold("GatewayAnswer")
    probe = asyncio.create_task(_ask(gw))
    await gate.arrived.wait()
    with pytest.raises(LLMUnavailableError) as second:
        await _ask(gw)
    assert second.value.reason == "circuit_open"
    gate.release.set()
    assert (await probe).value.label == "ok"
    assert len(provider.calls) == 6
    await _ask(gw)
    assert len(provider.calls) == 7

    other = _scripted()
    gw2 = _gw(db, other, role_prompts, redis, **over | {"llm_default_model": f"m-{uuid4().hex}"})
    for failures in (4, 0, 4):
        other.fail(ScriptedFailure("unavailable", times=failures or 1))
        for _ in range(failures):
            with pytest.raises(LLMUnavailableError):
                await _ask(gw2)
        if failures == 0:
            other.failures.clear()
            await _ask(gw2)  # a success resets the counter
    assert (await _ask(gw2)).value.label == "ok"


async def test_prompt_role_uses_other_template(
    db: SessionFactory, role_prompts: PromptRegistry
) -> None:
    """T-L6."""
    provider = _scripted()
    learner = uuid4()
    gw = _gw(db, provider, role_prompts)
    await _ask(gw, "teacher_fallback", prompt_role="teacher", learner_id=learner)
    [row] = await _rows(
        db, "SELECT role, prompt_hash, model FROM ops.llm_ledger WHERE learner_id = :l", l=learner
    )
    assert row.role == "teacher_fallback"
    assert row.prompt_hash == role_prompts.load("teacher", 1).sha256
    assert row.model == gw.settings.model_for_role("teacher_fallback")
    assert provider.calls[0].prompt == "teacher: x"


async def test_session_budget_exempt_roles(
    db: SessionFactory, role_prompts: PromptRegistry
) -> None:
    """T-L7."""
    learner, sid = uuid4(), uuid4()
    async with db() as session, session.begin():
        await session.execute(
            text(
                "INSERT INTO core.sessions (id, learner_id, domain_id, mode, status, started_at, "
                "last_activity_at, start_seq, tokens_used) VALUES (:id, :l, :d, 'learn', "
                "'active', now(), now(), 1, 1000)"
            ),
            {"id": sid, "l": learner, "d": uuid4()},
        )
    provider = _scripted()
    gw = _gw(db, provider, role_prompts, session_token_limit=1000)
    await _ask(gw, "extractor", learner_id=learner, session_id=sid)
    rows = await _rows(db, "SELECT role FROM ops.llm_ledger WHERE learner_id = :l", l=learner)
    assert [r.role for r in rows] == ["extractor"]
    with pytest.raises(LLMBudgetExceededError) as exc:
        await _ask(gw, "artifacts", learner_id=learner, session_id=sid)
    assert exc.value.scope == "session"


async def test_incomplete_response_retried_once_with_doubled_limit(
    db: SessionFactory, role_prompts: PromptRegistry
) -> None:
    """T-L11 (gateway part; parsing of `output` items is WP5.7)."""
    limits = {"extractor": 4096}
    provider = _scripted()
    provider.fail(ScriptedFailure("incomplete"))
    learner = uuid4()
    gw = _gw(db, provider, role_prompts, llm_role_max_output_tokens=limits)
    assert (await _ask(gw, learner_id=learner)).value.label == "ok"
    assert [c.max_output_tokens for c in provider.calls] == [4096, 8192]
    rows = await _rows(db, "SELECT status FROM ops.llm_ledger WHERE learner_id = :l", l=learner)
    assert sorted(r.status for r in rows) == ["incomplete", "ok"]

    twice = _scripted()
    twice.fail(ScriptedFailure("incomplete", times=2))
    with pytest.raises(LLMSchemaError) as exc:
        await _ask(_gw(db, twice, role_prompts, llm_role_max_output_tokens=limits))
    assert exc.value.reason == "incomplete"
    assert len(twice.calls) == 2

    capped = _scripted()
    capped.fail(ScriptedFailure("incomplete"))
    with pytest.raises(LLMSchemaError):
        await _ask(
            _gw(
                db,
                capped,
                role_prompts,
                llm_role_max_output_tokens={"extractor": 16384},
                max_output_tokens_cap=16384,
            )
        )
    assert len(capped.calls) == 1

    breaker = _scripted()
    breaker.fail(ScriptedFailure("incomplete", times=100))
    gw_b = _gw(db, breaker, role_prompts, llm_role_max_output_tokens=limits, circuit_failures=5)
    for _ in range(7):
        with pytest.raises(LLMSchemaError):
            await _ask(gw_b)
    assert len(breaker.calls) == 14  # every call reached the provider: the circuit stayed closed


async def test_bad_request_not_retried(db: SessionFactory, role_prompts: PromptRegistry) -> None:
    """T-L12."""
    provider = _scripted()
    provider.fail(ScriptedFailure("bad_request", times=5))
    gw = _gw(db, provider, role_prompts, circuit_failures=5)
    for _ in range(5):
        with pytest.raises(LLMUnavailableError) as exc, capture_logs() as logs:
            await _ask(gw)
        assert exc.value.reason == "bad_request"
        assert any("scripted bad request" in str(e.get("body")) for e in logs)
    assert len(provider.calls) == 5
    assert (await _ask(gw)).value.label == "ok"  # the circuit stayed closed


async def test_pressure_flag_from_ratelimit_headers(
    db: SessionFactory, redis: Redis, role_prompts: PromptRegistry
) -> None:
    """T-L13."""
    model, other = f"m-{uuid4().hex}", f"m-{uuid4().hex}"
    over: dict[str, Any] = {
        "llm_default_model": model,
        "llm_role_models": {"teacher": other},
        "pressure_remaining_share": 0.2,
        "pressure_header_ttl_s": 1,
    }

    async def answer(share: float | None) -> None:
        provider = _scripted()
        if share is not None:
            provider.fail(ScriptedFailure("ratelimit_headers", remaining_share=share))
        await _ask(_gw(db, provider, role_prompts, redis, **over))

    await answer(0.15)
    assert 0 < await _ttl(redis, model) <= 1
    assert await redis.exists(llm_pressure_key(other)) == 0
    await redis.delete(llm_pressure_key(model))
    await answer(0.5)
    assert await redis.exists(llm_pressure_key(model)) == 0
    await answer(None)
    assert await redis.exists(llm_pressure_key(model)) == 0
    # A long 429 flag is not shortened by a low-remaining answer.
    await set_pressure_flag(redis, model, 30, "429", datetime.now(UTC))
    await answer(0.1)
    assert await _ttl(redis, model) > 20
    await redis.delete(llm_pressure_key(model))


# --- WP5.7: ResponsesClient (T-L8, T-L9 instance, T-L10) --------------------------------


def test_strict_schema_minimal_instance_validates() -> None:
    """T-L9 (instance): the minimal valid instance of each strict schema (the generator of
    `meta-mock`) passes the schema (`jsonschema`) and `model_validate_json`."""
    for model in ROLE_OUTPUT_MODELS:
        schema = to_strict_json_schema(model)
        instance = minimal_instance(schema)
        jsonschema.validate(instance, schema)
        model.model_validate_json(orjson.dumps(instance))


class _OneTemplateRegistry(PromptRegistry):
    """Every `role@version` renders the same short template (T-L8 checks the request)."""

    def load(self, role: str, version: int) -> PromptTemplate:
        return PromptTemplate(role, version, "Answer as {{ role }}.", "0" * 64)


def _responses_body(
    answer: str,
    *,
    commentary: str | None = None,
    status: str = "completed",
    cached: int = 0,
    reasoning: int = 0,
) -> dict[str, Any]:
    output: list[dict[str, Any]] = [
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "opaque", "summary": []}
    ]
    if commentary is not None:
        output.append(
            {
                "type": "message",
                "phase": "commentary",
                "role": "assistant",
                "content": [{"type": "output_text", "text": commentary}],
            }
        )
    if answer:
        output.append(
            {
                "type": "message",
                "role": "assistant",
                "status": status,
                "content": [{"type": "output_text", "text": answer}],
            }
        )
    return {
        "id": "resp_1",
        "object": "response",
        "model": "m",
        "status": status,
        "output": output,
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": cached},
            "output_tokens": 40,
            "output_tokens_details": {"reasoning_tokens": reasoning},
            "total_tokens": 140,
        },
    }


class _MockResponses:
    """`httpx.MockTransport` handler: records requests, answers from a queue (the last
    answer repeats)."""

    def __init__(self, *answers: httpx.Response) -> None:
        self.answers = list(answers)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]

    def bodies(self) -> list[dict[str, Any]]:
        return [orjson.loads(r.content) for r in self.requests]


def _client(handler: _MockResponses) -> ResponsesClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ResponsesClient("http://meta.test/v1", "key", http_client=http)


def _minimal_answer(model: type[BaseModel]) -> str:
    return orjson.dumps(minimal_instance(to_strict_json_schema(model))).decode()


# Roles of Phase 3 and their output models (§7.6); `teacher_fallback` uses the teacher prompt.
ROLE_CALLS: tuple[tuple[str, type[BaseModel], str | None], ...] = (
    ("structurer", StructurerOutput, None),
    ("diagnostic", DiagnosticOutput, None),
    ("curator", CuratorOutput, None),
    ("teacher", TeacherOutput, None),
    ("teacher_fallback", TeacherOutput, "teacher"),
    ("extractor", ExtractorOutput, None),
    ("task_gen", TaskGenOutput, None),
    ("evaluator", EvaluatorOutput, None),
    ("artifacts", ArtifactGenOutput, None),
)


@pytest.mark.parametrize(
    ("role", "model", "prompt_role"), ROLE_CALLS, ids=[c[0] for c in ROLE_CALLS]
)
async def test_responses_request_contract(
    db: SessionFactory,
    settings: Settings,
    role: str,
    model: type[BaseModel],
    prompt_role: str | None,
) -> None:
    """T-L8: one `POST /responses` per call of each role; `model`, `store: false`,
    `text.format` (strict JSON Schema of the output), `reasoning.effort` and
    `max_output_tokens` of the role, `prompt_cache_key` = `{role}@{version}` (`teacher@N` for
    `teacher_fallback`); none of the forbidden keys. A 500 → exactly one HTTP request."""
    handler = _MockResponses(httpx.Response(200, json=_responses_body(_minimal_answer(model))))
    gateway = LLMGateway(db, _client(handler), settings, _OneTemplateRegistry())
    await gateway.generate_structured(
        role=role,
        prompt_version=1,
        response_model=model,
        variables={"role": role},
        learner_id=uuid4(),
        session_id=None,
        turn_id=None,
        idempotency_key=f"tl8:{role}:{uuid4()}",
        prompt_role=prompt_role,
    )
    (request,) = handler.requests
    assert request.method == "POST"
    assert request.url.path.endswith("/responses")
    (body,) = handler.bodies()
    assert body["model"] == settings.model_for_role(role)
    assert body["store"] is False
    assert body["text"]["format"] == {
        "type": "json_schema",
        "name": model.__name__,
        "strict": True,
        "schema": to_strict_json_schema(model),
    }
    assert body["reasoning"] == {"effort": settings.llm_role_reasoning[role]}
    assert body["max_output_tokens"] == settings.llm_role_max_output_tokens[role]
    assert body["prompt_cache_key"] == f"{prompt_role or role}@1"
    forbidden = {
        "temperature",
        "response_format",
        "prompt_cache_retention",
        "previous_response_id",
        "tools",
        "tool_choice",
    }
    assert forbidden.isdisjoint(body)

    failing = _MockResponses(httpx.Response(500, json={"error": {"message": "boom"}}))
    gateway = LLMGateway(db, _client(failing), settings, _OneTemplateRegistry())
    with pytest.raises(LLMUnavailableError):
        await gateway.generate_structured(
            role=role,
            prompt_version=1,
            response_model=model,
            variables={"role": role},
            learner_id=uuid4(),
            session_id=None,
            turn_id=None,
            idempotency_key=f"tl8-500:{role}:{uuid4()}",
            prompt_role=prompt_role,
        )
    assert len(failing.requests) == 1  # settings.llm_retry_attempts = 0; SDK max_retries = 0


async def test_output_items_parsing(db: SessionFactory, settings: Settings) -> None:
    """T-L10: `output` = [reasoning, commentary «Let me check…», message with JSON] → parsed
    from the last item, the commentary in the log, not in the parse; only a commentary → a
    schema error and one repair; JSON keys not in schema order → parsed; usage cached 120 and
    reasoning 300 → ledger `cached_input_tokens`, `reasoning_tokens`, `reasoning_effort`."""
    learner = uuid4()
    reordered = '{"reply_text": "Hi.", "pedagogical_move": "explain"}'
    handler = _MockResponses(
        httpx.Response(
            200,
            json=_responses_body(reordered, commentary="Let me check…", cached=120, reasoning=300),
        )
    )
    gateway = LLMGateway(db, _client(handler), settings, _OneTemplateRegistry())
    with capture_logs() as logs:
        result = await gateway.generate_structured(
            role="teacher",
            prompt_version=1,
            response_model=TeacherOutput,
            variables={"role": "teacher"},
            learner_id=learner,
            session_id=None,
            turn_id=None,
            idempotency_key=f"tl10:{uuid4()}",
        )
    assert result.value.reply_text == "Hi."
    assert any("Let me check" in repr(entry) for entry in logs)
    async with db() as session:
        row = (
            await session.execute(
                text(
                    "SELECT cached_input_tokens, reasoning_tokens, reasoning_effort "
                    "FROM ops.llm_ledger WHERE learner_id = :l"
                ),
                {"l": learner},
            )
        ).one()
    assert (row.cached_input_tokens, row.reasoning_tokens) == (120, 300)
    assert row.reasoning_effort == settings.llm_role_reasoning["teacher"]

    only_commentary = _MockResponses(
        httpx.Response(200, json=_responses_body("", commentary="Thinking it over.")),
        httpx.Response(200, json=_responses_body(reordered)),
    )
    gateway = LLMGateway(db, _client(only_commentary), settings, _OneTemplateRegistry())
    result = await gateway.generate_structured(
        role="teacher",
        prompt_version=1,
        response_model=TeacherOutput,
        variables={"role": "teacher"},
        learner_id=uuid4(),
        session_id=None,
        turn_id=None,
        idempotency_key=f"tl10-repair:{uuid4()}",
    )
    assert result.value.reply_text == "Hi."
    assert len(only_commentary.requests) == 2  # the answer and one repair
