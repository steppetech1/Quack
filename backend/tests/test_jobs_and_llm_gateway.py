"""ops.jobs protocol (spec §7, Т-§13) and LLMGateway (spec §8.3, Т-§11, Т-§12.4)."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import text

from quack.config import Settings
from quack.db.postgres import SessionFactory
from quack.jobs import kinds
from quack.jobs.queue import (
    JobSpec,
    backoff_seconds,
    claim_next_job,
    complete_job,
    enqueue_job,
    fail_or_retry_job,
    heartbeat_job,
    reap_stale_jobs,
)
from quack.llm.errors import (
    LLMBudgetExceededError,
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

# --- helpers ---------------------------------------------------------------------------


async def _q(db: SessionFactory, sql: str, **params: Any) -> list[dict[str, Any]]:
    async with db() as session:
        return [dict(r) for r in (await session.execute(text(sql), params)).mappings()]


async def _exec(db: SessionFactory, sql: str, **params: Any) -> None:
    async with db() as session, session.begin():
        await session.execute(text(sql), params)


async def _enqueue(db: SessionFactory, spec: JobSpec) -> UUID:
    async with db() as session, session.begin():
        return await enqueue_job(session, spec)


async def _claim(db: SessionFactory, worker: str = "w1", kinds_: list[str] | None = None):
    async with db() as session, session.begin():
        return await claim_next_job(session, worker, kinds_)


# --- Job kinds -------------------------------------------------------------------------


def test_job_kind_catalog() -> None:
    learner, sid, topic, domain = uuid4(), uuid4(), uuid4(), uuid4()
    extract = kinds.turn_extract(learner, sid, {}, required_seq=None)
    assert (extract.kind, extract.priority, extract.lock_key, extract.coalesce_key) == (
        "turn.extract",
        10,
        f"session:{sid}:extract",
        None,
    )
    cp = kinds.session_checkpoint(learner, sid, {})
    assert (cp.priority, cp.lock_key, cp.coalesce_key) == (
        20,
        f"session:{sid}:checkpoint",
        f"checkpoint:{sid}",
    )
    fin = kinds.session_finish_artifacts(learner, sid, {}, required_seq=None)
    assert (fin.priority, fin.lock_key, fin.coalesce_key) == (
        30,
        f"session:{sid}:finish",
        f"finish:{sid}",
    )
    sheet = kinds.artifact_refresh_cheatsheet(learner, topic, {}, required_seq=None)
    assert (sheet.priority, sheet.lock_key, sheet.coalesce_key) == (
        40,
        f"topic:{topic}:cheatsheet",
        f"cheatsheet:{topic}",
    )
    cur = kinds.graph_curate(learner, domain, {}, required_seq=None)
    assert (cur.priority, cur.lock_key, cur.coalesce_key) == (
        80,
        f"learner:{learner}:curate",
        f"curate:{domain}",
    )
    sweep = kinds.session_sweep_inactive()
    assert (sweep.priority, sweep.lock_key, sweep.coalesce_key) == (
        90,
        "global:sweep_inactive",
        "global:sweep_inactive",
    )


def test_backoff_formula() -> None:
    assert [backoff_seconds(a) for a in (0, 1, 2, 3, 5, 6, 10)] == [5, 10, 20, 40, 160, 300, 300]


# --- Coalescing ------------------------------------------------------------------------


async def test_coalesce_updates_queued_job_instead_of_duplicating(db: SessionFactory) -> None:
    learner, sid = uuid4(), uuid4()
    first = await _enqueue(db, kinds.session_checkpoint(learner, sid, {"up_to": 6}))
    second = await _enqueue(db, kinds.session_checkpoint(learner, sid, {"up_to": 12}))
    assert first == second
    rows = await _q(db, "SELECT id, payload, status FROM ops.jobs")
    assert len(rows) == 1
    assert rows[0]["payload"] == {"up_to": 12} and rows[0]["status"] == "queued"


async def test_coalesce_only_while_queued(db: SessionFactory) -> None:
    learner, sid = uuid4(), uuid4()
    first = await _enqueue(db, kinds.session_checkpoint(learner, sid, {"n": 1}))
    claimed = await _claim(db)
    assert claimed is not None and claimed.id == first
    second = await _enqueue(db, kinds.session_checkpoint(learner, sid, {"n": 2}))
    assert second != first
    statuses = {r["id"]: r["status"] for r in await _q(db, "SELECT id, status FROM ops.jobs")}
    assert statuses == {first: "running", second: "queued"}


async def test_jobs_without_coalesce_key_are_not_merged(db: SessionFactory) -> None:
    learner, sid = uuid4(), uuid4()
    a = await _enqueue(db, kinds.turn_extract(learner, sid, {"turn": 1}, required_seq=None))
    b = await _enqueue(db, kinds.turn_extract(learner, sid, {"turn": 2}, required_seq=None))
    assert a != b
    assert len(await _q(db, "SELECT id FROM ops.jobs")) == 2


async def test_enqueue_inside_append_transaction_rolls_back_together(db: SessionFactory) -> None:
    async with db() as session:
        await session.begin()
        await enqueue_job(session, kinds.turn_extract(uuid4(), uuid4(), {}, required_seq=None))
        await session.rollback()
    assert await _q(db, "SELECT id FROM ops.jobs") == []


# --- Claiming and lock_key exclusion ---------------------------------------------------


async def test_lock_key_mutual_exclusion(db: SessionFactory) -> None:
    learner, sid = uuid4(), uuid4()
    a = await _enqueue(db, kinds.turn_extract(learner, sid, {"turn": 1}, required_seq=None))
    b = await _enqueue(db, kinds.turn_extract(learner, sid, {"turn": 2}, required_seq=None))
    first = await _claim(db, "w1")
    assert first is not None and first.id == a
    assert await _claim(db, "w2") is None  # same lock_key is running
    other = await _enqueue(db, kinds.turn_extract(learner, uuid4(), {}, required_seq=None))
    got = await _claim(db, "w2")
    assert got is not None and got.id == other
    async with db() as session, session.begin():
        assert await complete_job(session, a, "w1")
    nxt = await _claim(db, "w3")
    assert nxt is not None and nxt.id == b


async def test_concurrent_claims_never_run_same_lock_twice(db: SessionFactory) -> None:
    learner, sid = uuid4(), uuid4()
    for i in range(6):
        await _enqueue(db, kinds.turn_extract(learner, sid, {"turn": i}, required_seq=None))
    results = await asyncio.gather(*(_claim(db, f"w{i}") for i in range(6)))
    assert len([r for r in results if r is not None]) == 1
    running = await _q(db, "SELECT id FROM ops.jobs WHERE status = 'running'")
    assert len(running) == 1


async def test_priority_then_age_ordering(db: SessionFactory) -> None:
    learner = uuid4()
    low = await _enqueue(db, kinds.graph_curate(learner, uuid4(), {}, required_seq=None))
    high = await _enqueue(db, kinds.turn_extract(learner, uuid4(), {}, required_seq=None))
    mid = await _enqueue(db, kinds.session_checkpoint(learner, uuid4(), {}))
    order = []
    for w in range(3):
        job = await _claim(db, f"w{w}")
        assert job is not None
        order.append(job.id)
    assert order == [high, mid, low]


async def test_claim_filters_by_kind_and_run_after(db: SessionFactory) -> None:
    future = datetime.now(UTC) + timedelta(minutes=5)
    spec = kinds.turn_extract(uuid4(), uuid4(), {}, required_seq=None)
    await _enqueue(db, JobSpec(**{**spec.__dict__, "run_after": future}))
    assert await _claim(db) is None
    now_job = await _enqueue(db, kinds.session_checkpoint(uuid4(), uuid4(), {}))
    assert await _claim(db, kinds_=["turn.extract"]) is None
    got = await _claim(db, kinds_=["session.checkpoint"])
    assert got is not None and got.id == now_job


async def test_heartbeat_and_completion_require_owner(db: SessionFactory) -> None:
    job_id = await _enqueue(db, kinds.turn_extract(uuid4(), uuid4(), {}, required_seq=None))
    await _claim(db, "w1")
    async with db() as session, session.begin():
        assert await heartbeat_job(session, job_id, "w1") is True
        assert await heartbeat_job(session, job_id, "w2") is False
        assert await complete_job(session, job_id, "w2") is False
        assert await complete_job(session, job_id, "w1") is True
    [row] = await _q(db, "SELECT status, locked_by FROM ops.jobs")
    assert row["status"] == "done"


# --- Retries and DLQ -------------------------------------------------------------------


async def test_retry_with_backoff_then_dlq(db: SessionFactory) -> None:
    spec = kinds.turn_extract(uuid4(), uuid4(), {"x": 1}, required_seq=None)
    job_id = await _enqueue(db, JobSpec(**{**spec.__dict__, "max_attempts": 3}))
    for attempt in (1, 2):
        claimed = await _claim(db)
        assert claimed is not None and claimed.id == job_id
        before = datetime.now(UTC)
        async with db() as session, session.begin():
            decision = await fail_or_retry_job(session, job_id, "w1", RuntimeError("boom"))
        assert decision.status == "queued" and decision.attempt == attempt
        assert decision.run_after is not None
        expected = backoff_seconds(attempt)
        assert decision.run_after >= before + timedelta(seconds=expected - 2)
        assert decision.run_after <= before + timedelta(seconds=expected + 5)
        assert await _claim(db) is None  # backoff not elapsed
        await _exec(db, "UPDATE ops.jobs SET run_after = now() - interval '1 second'")
    claimed = await _claim(db)
    assert claimed is not None
    async with db() as session, session.begin():
        decision = await fail_or_retry_job(
            session, job_id, "w1", ValueError("final"), stacktrace="tb"
        )
    assert decision.status == "failed" and decision.attempt == 3 and decision.dlq_id is not None
    [job] = await _q(db, "SELECT status, attempt, last_error FROM ops.jobs")
    assert job["status"] == "failed" and job["attempt"] == 3 and "final" in job["last_error"]
    [dlq] = await _q(db, "SELECT * FROM ops.dlq")
    assert dlq["source"] == "job" and dlq["job_id"] == job_id and dlq["kind"] == "turn.extract"
    assert dlq["error_type"] == "ValueError" and dlq["error_message"] == "final"
    assert dlq["stacktrace"] == "tb" and dlq["payload"]["payload"] == {"x": 1}
    assert dlq["replayed_at"] is None


async def test_stale_running_jobs_are_reaped(db: SessionFactory) -> None:
    job_id = await _enqueue(db, kinds.turn_extract(uuid4(), uuid4(), {}, required_seq=None))
    await _claim(db, "dead-worker")
    async with db() as session, session.begin():
        assert await reap_stale_jobs(session, stale_after_sec=60) == 0
    await _exec(db, "UPDATE ops.jobs SET heartbeat_at = now() - interval '2 minutes'")
    async with db() as session, session.begin():
        assert await reap_stale_jobs(session, stale_after_sec=60) == 1
    [row] = await _q(
        db, "SELECT status, attempt, locked_by FROM ops.jobs WHERE id = :id", id=job_id
    )
    assert row["status"] == "queued" and row["attempt"] == 1 and row["locked_by"] is None


async def test_stale_job_at_max_attempts_goes_to_dlq(db: SessionFactory) -> None:
    spec = kinds.turn_extract(uuid4(), uuid4(), {}, required_seq=None)
    await _enqueue(db, JobSpec(**{**spec.__dict__, "max_attempts": 1}))
    await _claim(db, "dead")
    await _exec(db, "UPDATE ops.jobs SET heartbeat_at = now() - interval '2 minutes'")
    async with db() as session, session.begin():
        assert await reap_stale_jobs(session, stale_after_sec=60) == 1
    [row] = await _q(db, "SELECT status FROM ops.jobs")
    assert row["status"] == "failed"
    assert len(await _q(db, "SELECT id FROM ops.dlq WHERE source = 'job'")) == 1


async def test_retry_of_coalesced_job_merges_with_newer_queued(db: SessionFactory) -> None:
    learner, sid = uuid4(), uuid4()
    first = await _enqueue(db, kinds.session_checkpoint(learner, sid, {"n": 1}))
    await _claim(db)
    newer = await _enqueue(db, kinds.session_checkpoint(learner, sid, {"n": 2}))
    async with db() as session, session.begin():
        decision = await fail_or_retry_job(session, first, "w1", RuntimeError("x"))
    assert decision.status == "cancelled"
    statuses = {r["id"]: r["status"] for r in await _q(db, "SELECT id, status FROM ops.jobs")}
    assert statuses == {first: "cancelled", newer: "queued"}


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


def _gateway(db: SessionFactory, provider: FakeProvider, prompts: PromptRegistry, **over: Any):
    settings = Settings(env="test", **over)
    return LLMGateway(db, provider, settings, prompts)


async def _session_row(db: SessionFactory, learner: UUID, tokens: int = 0) -> UUID:
    sid = uuid4()
    await _exec(
        db,
        "INSERT INTO core.sessions (id, learner_id, domain_id, mode, status, started_at, "
        "last_activity_at, start_seq, tokens_used) VALUES (:id, :l, :d, 'learn', 'active', "
        "now(), now(), 1, :t)",
        id=sid,
        l=learner,
        d=uuid4(),
        t=tokens,
    )
    return sid


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


async def test_gateway_call_writes_ledger_outbox_and_session_tokens(
    db: SessionFactory, prompts: PromptRegistry
) -> None:
    provider = FakeProvider()
    gw = _gateway(db, provider, prompts)
    learner = uuid4()
    sid = await _session_row(db, learner)
    result = await _generate(gw, learner, sid, "obs:1")
    assert result.value == Answer(label="ok", confidence=0.9)
    assert result.from_cache is False
    assert result.prompt_hash == prompts.load("extractor", 1).sha256
    assert len(provider.calls) == 1
    request = provider.calls[0]
    assert request.prompt == "Classify: x^2"
    assert request.json_schema == to_strict_json_schema(Answer)  # WP5.7: the strict schema
    [ledger] = await _q(db, "SELECT * FROM ops.llm_ledger")
    assert ledger["role"] == "extractor" and ledger["prompt_version"] == 1
    assert ledger["input_tokens"] == 100 and ledger["output_tokens"] == 20
    assert ledger["cached_tokens"] == 10 and ledger["status"] == "ok"
    assert ledger["prompt_hash"] == result.prompt_hash
    [outbox] = await _q(db, "SELECT * FROM core.llm_outbox")
    assert outbox["status"] == "completed" and outbox["response_json"] == result.value.model_dump()
    [sess] = await _q(db, "SELECT tokens_used FROM core.sessions WHERE id = :id", id=sid)
    assert sess["tokens_used"] == 120


async def test_gateway_replay_is_served_from_outbox(
    db: SessionFactory, prompts: PromptRegistry
) -> None:
    provider = FakeProvider()
    gw = _gateway(db, provider, prompts)
    learner = uuid4()
    first = await _generate(gw, learner, None, "obs:2")
    again = await _generate(gw, learner, None, "obs:2")
    assert again.from_cache is True
    assert again.value == first.value
    assert len(provider.calls) == 1
    assert len(await _q(db, "SELECT id FROM ops.llm_ledger")) == 1
    other_learner = await _generate(gw, uuid4(), None, "obs:2")
    assert other_learner.from_cache is False and len(provider.calls) == 2


async def test_gateway_daily_budget(db: SessionFactory, prompts: PromptRegistry) -> None:
    provider = FakeProvider()
    gw = _gateway(db, provider, prompts, daily_token_limit_per_learner=1000)
    learner = uuid4()
    await _exec(
        db,
        "INSERT INTO ops.llm_ledger (id, learner_id, role, prompt_version, prompt_hash, model, "
        "input_tokens, output_tokens, latency_ms, status, created_at) VALUES "
        "(:id, :l, 'teacher', 1, 'h', 'm', 900, 100, 5, 'ok', now())",
        id=uuid4(),
        l=learner,
    )
    # Yesterday's usage does not count.
    await _exec(
        db,
        "INSERT INTO ops.llm_ledger (id, learner_id, role, prompt_version, prompt_hash, model, "
        "input_tokens, output_tokens, latency_ms, status, created_at) VALUES "
        "(:id, :l, 'teacher', 1, 'h', 'm', 5000, 0, 5, 'ok', now() - interval '2 days')",
        id=uuid4(),
        l=learner,
    )
    with pytest.raises(LLMBudgetExceededError) as exc:
        await _generate(gw, learner, None, "obs:3")
    assert exc.value.scope == "daily"
    assert provider.calls == []
    fresh = uuid4()
    await _exec(
        db,
        "INSERT INTO ops.llm_ledger (id, learner_id, role, prompt_version, prompt_hash, model, "
        "input_tokens, output_tokens, latency_ms, status, created_at) VALUES "
        "(:id, :l, 'teacher', 1, 'h', 'm', 5000, 0, 5, 'ok', now() - interval '2 days')",
        id=uuid4(),
        l=fresh,
    )
    assert (await _generate(gw, fresh, None, "obs:4")).from_cache is False


async def test_gateway_session_budget(db: SessionFactory, prompts: PromptRegistry) -> None:
    provider = FakeProvider()
    # WP5.6: `extractor` is exempt from the session budget; this test checks the budget itself.
    gw = _gateway(db, provider, prompts, session_token_limit=500, session_budget_exempt_roles=())
    learner = uuid4()
    sid = await _session_row(db, learner, tokens=500)
    with pytest.raises(LLMBudgetExceededError) as exc:
        await _generate(gw, learner, sid, "obs:5")
    assert exc.value.scope == "session"
    assert provider.calls == []


async def test_gateway_cached_result_is_returned_even_over_budget(
    db: SessionFactory, prompts: PromptRegistry
) -> None:
    provider = FakeProvider()
    learner = uuid4()
    await _generate(_gateway(db, provider, prompts), learner, None, "obs:6")
    tight = _gateway(db, provider, prompts, daily_token_limit_per_learner=1)
    assert (await _generate(tight, learner, None, "obs:6")).from_cache is True


async def test_gateway_provider_unavailable(db: SessionFactory, prompts: PromptRegistry) -> None:
    provider = FakeProvider([ProviderUnavailableError("503")])
    gw = _gateway(db, provider, prompts, llm_retry_attempts=0)  # one failure, no WP5.2 retry
    learner = uuid4()
    with pytest.raises(LLMUnavailableError):
        await _generate(gw, learner, None, "obs:7")
    [ledger] = await _q(db, "SELECT status, error_code FROM ops.llm_ledger")
    assert ledger["status"] == "error" and ledger["error_code"] == "unavailable"
    [outbox] = await _q(db, "SELECT status FROM core.llm_outbox")
    assert outbox["status"] == "failed"
    # A retry with the same key calls the provider again (nothing was completed).
    assert (await _generate(gw, learner, None, "obs:7")).from_cache is False


async def test_gateway_repairs_schema_once(db: SessionFactory, prompts: PromptRegistry) -> None:
    provider = FakeProvider(['{"label": 1}', '{"label": "fixed", "confidence": 0.5}'])
    gw = _gateway(db, provider, prompts)
    result = await _generate(gw, uuid4(), None, "obs:8")
    assert result.value.label == "fixed"
    assert len(provider.calls) == 2
    assert provider.calls[1].repair_hint is not None
    bad = FakeProvider(["not json", '{"label": 2}'])
    with pytest.raises(LLMSchemaError):
        await _generate(_gateway(db, bad, prompts), uuid4(), None, "obs:9")
    assert len(bad.calls) == 2


async def test_gateway_semaphore_limits_concurrency(
    db: SessionFactory, prompts: PromptRegistry
) -> None:
    # §8.9 п.4: semaphores per model (WP5.1), same point: concurrency is bounded by the limit.
    provider = FakeProvider(delay=0.05)
    limits = {"api": {"muse-spark": 2}}
    gw = _gateway(
        db,
        provider,
        prompts,
        process_role="api",
        llm_concurrency=limits,
        llm_default_model="muse-spark",
    )
    learner = uuid4()
    await asyncio.gather(*(_generate(gw, learner, None, f"par:{i}") for i in range(6)))
    assert len(provider.calls) == 6
    assert provider.max_active <= 2
