"""The backoff of the job queue (Т-§13.1), the prompt registry and the LLMGateway (spec §8.3,
Т-§11, Т-§12.4). The queue itself is in `tests/jobs/test_queue_p4.py` (D-A-231)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel

from quack.config import Settings
from quack.db import Db
from quack.jobs.queue_p4 import backoff_seconds
from quack.llm.errors import (
    LLMSchemaError,
    LLMUnavailableError,
    ProviderUnavailableError,
)
from quack.llm.gateway import LLMGateway
from quack.llm.provider import ProviderRequest, ProviderResponse
from quack.llm.strict_schema import to_strict_json_schema
from quack.prompts.registry import (
    PromptHashMismatchError,
    PromptNotFoundError,
    PromptRegistry,
    PromptRenderError,
)

# --- Retry pause -----------------------------------------------------------------------


def test_backoff_formula() -> None:
    """`min(cap, base * 2 ** attempts)` with a spread of ±25 %, never above the cap."""
    settings = Settings(job_backoff_base_sec=5, job_backoff_max_sec=300)
    middle = [backoff_seconds(a, settings, rand=lambda: 0.5) for a in (0, 1, 2, 3, 5, 6, 10)]
    assert middle == [5, 10, 20, 40, 160, 300, 300]
    assert backoff_seconds(2, settings, rand=lambda: 0.0) == 15  # 20 * 0.75
    assert backoff_seconds(2, settings, rand=lambda: 1.0) == 25  # 20 * 1.25
    assert backoff_seconds(10, settings, rand=lambda: 1.0) == 300  # the cap holds with the spread


# --- Prompt registry -------------------------------------------------------------------


def _write_prompt(base: Path, role: str, version: int, body: str, *, register: bool = True) -> None:
    (base / role).mkdir(parents=True, exist_ok=True)
    (base / role / f"v{version}.md").write_text(body, encoding="utf-8")
    manifest_path = base / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if register:
        manifest[f"{role}@{version}"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    manifest_path.write_text(json.dumps(manifest))


def test_prompt_registry_loads_and_verifies_hash(tmp_path: Path) -> None:
    _write_prompt(tmp_path, "extractor", 1, "Extract from {{ turn_text }} for {{learner}}.")
    registry = PromptRegistry(tmp_path)
    prompt = registry.load("extractor", 1)
    assert prompt.ref == "extractor@1"
    assert prompt.sha256 == hashlib.sha256(prompt.text.encode()).hexdigest()
    assert prompt.render({"turn_text": "x^2", "learner": "L"}) == "Extract from x^2 for L."
    with pytest.raises(PromptRenderError):
        prompt.render({"turn_text": "x"})
    (tmp_path / "extractor" / "v1.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(PromptHashMismatchError):
        registry.load("extractor", 1)
    with pytest.raises(PromptNotFoundError):
        registry.load("extractor", 2)
    _write_prompt(tmp_path, "teacher", 1, "unregistered", register=False)
    with pytest.raises(PromptNotFoundError):
        registry.load("teacher", 1)


def test_packaged_prompt_manifest_is_consistent() -> None:
    registry = PromptRegistry()
    manifest = json.loads((registry.base_dir / "manifest.json").read_text())
    for ref in manifest:
        role, version = ref.split("@")
        registry.load(role, int(version))


# --- LLM gateway -----------------------------------------------------------------------


class Answer(BaseModel):
    label: str
    confidence: float


class FakeProvider:
    def __init__(self, responses: list[str | Exception] | None = None, delay: float = 0.0):
        self.responses = list(responses or [])
        self.calls: list[ProviderRequest] = []
        self.delay = delay
        self.active = 0
        self.max_active = 0

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            item = self.responses.pop(0) if self.responses else '{"label": "ok", "confidence": 0.9}'
            if isinstance(item, Exception):
                raise item
            return ProviderResponse(
                content=item,
                input_tokens=100,
                output_tokens=20,
                cached_tokens=10,
                model=request.model,
            )
        finally:
            self.active -= 1


@pytest.fixture
def prompts(tmp_path: Path) -> PromptRegistry:
    _write_prompt(tmp_path, "extractor", 1, "Classify: {{ text }}")
    return PromptRegistry(tmp_path)


def _gateway(pg_db: Db, provider: FakeProvider, prompts: PromptRegistry, **over: Any):
    settings = Settings(env="test", **over)
    return LLMGateway(provider, settings, prompts, db=pg_db)


async def _usage(pg_db: Db, learner: UUID) -> list[dict[str, Any]]:
    return await pg_db.fetch_all(
        "SELECT role, prompt_version, input_tokens, output_tokens, cached_input_tokens, status "
        "FROM ops.llm_usage WHERE learner_id = %(l)s ORDER BY id",
        {"l": learner},
    )


async def _generate(gw: LLMGateway, learner: UUID, sid: UUID | None, key: str):
    return await gw.generate_structured(
        role="extractor",
        prompt_version=1,
        response_model=Answer,
        variables={"text": "x^2"},
        learner_id=learner,
        session_id=sid,
        turn_id=None,
        idempotency_key=key,
    )


async def test_gateway_call_writes_a_usage_row_and_a_repeat_pays_again(
    pg_db: Db, prompts: PromptRegistry
) -> None:
    provider = FakeProvider()
    gw = _gateway(pg_db, provider, prompts)
    learner = uuid4()
    result = await _generate(gw, learner, None, "obs:1")
    assert result.value == Answer(label="ok", confidence=0.9)
    assert result.from_cache is False
    assert result.prompt_hash == prompts.load("extractor", 1).sha256
    assert len(provider.calls) == 1
    request = provider.calls[0]
    assert request.prompt == "Classify: x^2"
    assert request.json_schema == to_strict_json_schema(Answer)  # WP5.7: the strict schema
    [row] = await _usage(pg_db, learner)
    assert (row["role"], row["prompt_version"], row["status"]) == ("extractor", "extractor@1", "ok")
    assert (row["input_tokens"], row["output_tokens"], row["cached_input_tokens"]) == (100, 20, 10)
    # D-B-212: no replay by the idempotency key — the repeat calls the model and pays again.
    await _generate(gw, learner, None, "obs:1")
    assert len(provider.calls) == 2 and len(await _usage(pg_db, learner)) == 2


async def test_gateway_provider_unavailable(pg_db: Db, prompts: PromptRegistry) -> None:
    provider = FakeProvider([ProviderUnavailableError("503")])
    gw = _gateway(pg_db, provider, prompts, llm_retry_attempts=0)  # one failure, no WP5.2 retry
    learner = uuid4()
    with pytest.raises(LLMUnavailableError):
        await _generate(gw, learner, None, "obs:7")
    [row] = await _usage(pg_db, learner)
    assert row["status"] == "error"
    # A retry with the same key calls the provider again.
    assert (await _generate(gw, learner, None, "obs:7")).value.label == "ok"


async def test_gateway_repairs_schema_once(pg_db: Db, prompts: PromptRegistry) -> None:
    provider = FakeProvider(['{"label": 1}', '{"label": "fixed", "confidence": 0.5}'])
    gw = _gateway(pg_db, provider, prompts)
    result = await _generate(gw, uuid4(), None, "obs:8")
    assert result.value.label == "fixed"
    assert len(provider.calls) == 2
    assert provider.calls[1].repair_hint is not None
    bad = FakeProvider(["not json", '{"label": 2}'])
    with pytest.raises(LLMSchemaError):
        await _generate(_gateway(pg_db, bad, prompts), uuid4(), None, "obs:9")
    assert len(bad.calls) == 2


async def test_gateway_semaphore_limits_concurrency(pg_db: Db, prompts: PromptRegistry) -> None:
    # §8.9 п.4: semaphores per model (WP5.1), same point: concurrency is bounded by the limit.
    provider = FakeProvider(delay=0.05)
    limits = {"api": {"muse-spark": 2}}
    gw = _gateway(
        pg_db,
        provider,
        prompts,
        process_role="api",
        llm_concurrency=limits,
        llm_role_models={"extractor": "muse-spark"},
    )
    learner = uuid4()
    await asyncio.gather(*(_generate(gw, learner, None, f"par:{i}") for i in range(6)))
    assert len(provider.calls) == 6
    assert provider.max_active <= 2
