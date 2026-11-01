"""`ScriptedLLMProvider` of `tests/support` (ТЗ Фазы 3 §8.1, seam S9 over S12)."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from quack.artifacts.schemas import ArtifactGenOutput
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.errors import (
    ProviderBadRequestError,
    ProviderIncompleteError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from quack.llm.provider import ProviderRequest
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.session.schemas import ExtractorOutput, ExtractorOutputV2, TeacherOutput
from tests.support.llm import ScriptedFailure, ScriptedLLMProvider

OUTPUT_SCHEMAS: tuple[type[BaseModel], ...] = (
    StructurerOutput,
    DiagnosticOutput,
    CuratorOutput,
    TeacherOutput,
    ExtractorOutput,
    ExtractorOutputV2,
    TaskGenOutput,
    EvaluatorOutput,
    ArtifactGenOutput,
)


def _request(schema_name: str, model: str = "muse-spark-1.3") -> ProviderRequest:
    return ProviderRequest(model, "prompt", {}, schema_name, 0.0, 1.0)


async def test_default_responders_are_valid_for_every_output_schema() -> None:
    provider = ScriptedLLMProvider()
    for model in OUTPUT_SCHEMAS:
        response = await provider.complete_structured(_request(model.__name__))
        model.model_validate_json(response.content)
    assert len(provider.calls_for(schema_name="TeacherOutput")) == 1


async def test_answer_is_chosen_by_schema_not_by_order() -> None:
    provider = ScriptedLLMProvider()
    provider.respond("ArtifactGenOutput", lambda _r: {"title": "T", "text": "x"})
    other = await provider.complete_structured(_request("ExtractorOutputV2"))
    mine = await provider.complete_structured(_request("ArtifactGenOutput"))
    assert ArtifactGenOutput.model_validate_json(mine.content).title == "T"
    ExtractorOutputV2.model_validate_json(other.content)


@pytest.mark.parametrize(
    ("kind", "error"),
    [
        ("unavailable", ProviderUnavailableError),
        ("bad_request", ProviderBadRequestError),
        ("incomplete", ProviderIncompleteError),
    ],
)
async def test_failures_raise_provider_errors(kind: str, error: type[Exception]) -> None:
    provider = ScriptedLLMProvider()
    provider.fail(ScriptedFailure(kind, schema_name="TeacherOutput"))
    with pytest.raises(error):
        await provider.complete_structured(_request("TeacherOutput"))
    # `times=1`: the next call answers normally; other schemas were never affected.
    TeacherOutput.model_validate_json(
        (await provider.complete_structured(_request("TeacherOutput"))).content
    )


@pytest.mark.parametrize(
    ("retry_after", "seconds"), [("2", 2.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None), (None, None)]
)
async def test_rate_limited_carries_raw_retry_after(
    retry_after: str | None, seconds: float | None
) -> None:
    provider = ScriptedLLMProvider()
    provider.fail(ScriptedFailure("rate_limited", model="muse-wonder-1.0", retry_after=retry_after))
    TeacherOutput.model_validate_json(
        (await provider.complete_structured(_request("TeacherOutput"))).content
    )
    with pytest.raises(ProviderRateLimitedError) as caught:
        await provider.complete_structured(_request("TeacherOutput", "muse-wonder-1.0"))
    assert caught.value.retry_after_sec == seconds
    assert caught.value.headers.get("retry-after") == retry_after


async def test_broken_answers_and_ratelimit_headers() -> None:
    provider = ScriptedLLMProvider()
    provider.fail(ScriptedFailure("invalid_json", schema_name="ExtractorOutput"))
    provider.fail(ScriptedFailure("commentary_only", schema_name="TeacherOutput"))
    provider.fail(
        ScriptedFailure("ratelimit_headers", schema_name="EvaluatorOutput", remaining_share=0.1)
    )
    assert (await provider.complete_structured(_request("ExtractorOutput"))).content == "{not json"
    commentary = await provider.complete_structured(_request("TeacherOutput"))
    assert commentary.content == ""
    assert [item["phase"] for item in commentary.output_items] == ["commentary"]
    limited = await provider.complete_structured(_request("EvaluatorOutput"))
    EvaluatorOutput.model_validate_json(limited.content)
    remaining = int(limited.headers["x-ratelimit-remaining-requests"])
    assert remaining == int(limited.headers["x-ratelimit-limit-requests"]) // 10


async def test_hold_blocks_until_released() -> None:
    provider = ScriptedLLMProvider()
    gate = provider.hold("TeacherOutput")
    call = asyncio.create_task(provider.complete_structured(_request("TeacherOutput")))
    await asyncio.wait_for(gate.arrived.wait(), timeout=5)
    assert not call.done()
    gate.release.set()
    TeacherOutput.model_validate_json((await asyncio.wait_for(call, timeout=5)).content)


def test_unknown_failure_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown failure kind"):
        ScriptedLLMProvider().fail(ScriptedFailure("teapot"))
