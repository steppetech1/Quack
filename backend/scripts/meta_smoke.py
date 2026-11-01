"""Live smoke of the Meta API: one call of every Phase 3 role through the gateway
(ТЗ Фазы 3 WP5.7, DoD п.6a). Not part of `pytest` or CI.

Every call goes through `LLMGateway` (strict schema, effort, output limit, ledger and outbox)
on `ResponsesClient`; a recording wrapper keeps the raw answer to report the token kinds and
the rate-limit headers. One line of JSON per role: model, `effort`, status, tokens by kind,
`x-ratelimit-*` / `retry-after`, latency. Exit code 1 if any role failed.

Run from `backend/` against a migrated Postgres (rows go to `ops.llm_ledger` and
`core.llm_outbox` under a fresh learner id):

    META_API_KEY=... META_API_BASE_URL=... POSTGRES_DSN=... uv run python scripts/meta_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quack.artifacts.schemas import ArtifactGenOutput
from quack.config import Settings
from quack.db.postgres import SessionFactory, create_engine, create_session_factory
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.errors import LLMError
from quack.llm.gateway import LLMGateway
from quack.llm.provider import (
    RATE_LIMIT_HEADERS,
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
)
from quack.llm.responses_client import ResponsesClient
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.prompts.registry import PromptRegistry
from quack.session.schemas import ExtractorOutputV2, TeacherOutput

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

# Input data of the smoke calls (not prompt text): one small case of a calculus topic.
_SKILLS = [{"label": "S1", "name": "Power rule"}, {"label": "S2", "name": "Chain rule"}]
_COMMON: dict[str, Any] = {
    "topic_name": "Derivatives",
    "domain_name": "Calculus",
    "skills_slice_json": json.dumps(_SKILLS),
    "misconceptions_json": "[]",
    "available_tasks_json": json.dumps([{"label": "K1", "prompt": "Differentiate x^3."}]),
    "task_evaluation_json": "none",
    "rolling_summary": "",
    "recent_turns_json": "[]",
    "student_text": "How do I differentiate x^3?",
    "guard_reason": "",
    "known_misconceptions": "none",
    "candidate_skills": "S1: Power rule\nS2: Chain rule",
    "events_before": "(none)",
    "episode": "[1] learner: is the derivative of x^3 equal to 3x^2?\n[2] teacher: why?",
    "prompt_text": "Explain why the derivative of x^2 is 2x.",
    "rubric": "R1: uses the limit of the difference quotient.",
    "reference_solution_json": json.dumps({"key_points": ["difference quotient"]}),
    "student_answer": "Because (x+h)^2 - x^2 over h tends to 2x.",
    "target_skill": "S1: Power rule",
    "target_level": "3",
    "kind": "math",
    "excluded_families": "none",
    "goals": "Pass the calculus exam",
    "background": "School algebra",
    "focus_areas": "Derivatives",
    "topics_json": json.dumps([{"slug": "derivatives", "name": "Derivatives"}]),
    "skills_json": json.dumps([{"slug": "power-rule", "name": "Power rule"}]),
    "skill_name": "Power rule",
    "question_prompt": "What is the derivative of x^3?",
    "title_context": "Derivatives",
    "skills_summary_json": json.dumps(_SKILLS),
    "misconceptions_summary_json": "[]",
    "dialogue_excerpt_json": json.dumps(
        [{"role": "student", "text": "x^3 gives 3x^2?"}, {"role": "teacher", "text": "Yes."}]
    ),
    "previous_content_md": "",
}


@dataclass(frozen=True)
class SmokeCall:
    role: str
    prompt_version: int
    response_model: type[BaseModel]
    prompt_role: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)


def smoke_calls() -> list[SmokeCall]:
    """One call per Phase 3 prompt (ТЗ §7.6), plus `teacher_fallback` on the teacher prompt."""
    return [
        SmokeCall("teacher", 1, TeacherOutput, overrides={"mode": "learn"}),
        SmokeCall(
            "teacher_fallback", 1, TeacherOutput, prompt_role="teacher", overrides={"mode": "learn"}
        ),
        SmokeCall("extractor", 2, ExtractorOutputV2),
        SmokeCall("evaluator", 1, EvaluatorOutput),
        SmokeCall("task_gen", 1, TaskGenOutput),
        SmokeCall("structurer", 1, StructurerOutput),
        SmokeCall("curator", 1, CuratorOutput),
        SmokeCall("diagnostic", 1, DiagnosticOutput, overrides={"mode": "generate"}),
        SmokeCall("artifacts", 1, ArtifactGenOutput, overrides={"mode": "digest"}),
    ]


def _variables(call: SmokeCall, registry: PromptRegistry) -> dict[str, Any]:
    template = registry.load(call.prompt_role or call.role, call.prompt_version)
    names = dict.fromkeys(_PLACEHOLDER.findall(template.text))
    values = {**_COMMON, **call.overrides}
    return {name: values.get(name, "none") for name in names}


@dataclass
class _Recorder:
    """Keeps the last raw answer of the provider for the report."""

    provider: LLMProvider
    last: ProviderResponse | None = None

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.last = None
        response = await self.provider.complete_structured(request)
        self.last = response
        return response


async def run_smoke(
    settings: Settings, session_factory: SessionFactory, provider: LLMProvider
) -> list[dict[str, Any]]:
    """Run every smoke call once; one report row per call."""
    recorder = _Recorder(provider)
    gateway = LLMGateway(session_factory, recorder, settings)
    registry = PromptRegistry()
    learner, run = uuid4(), uuid4().hex[:8]
    rows: list[dict[str, Any]] = []
    for call in smoke_calls():
        started = time.monotonic()
        status = "ok"
        try:
            await gateway.generate_structured(
                role=call.role,
                prompt_version=call.prompt_version,
                response_model=call.response_model,
                variables=_variables(call, registry),
                learner_id=learner,
                session_id=None,
                turn_id=None,
                idempotency_key=f"meta-smoke:{run}:{call.role}",
                prompt_role=call.prompt_role,
            )
        except LLMError as exc:
            status = f"{type(exc).__name__}:{getattr(exc, 'reason', '')}"
        last = recorder.last
        headers = dict(last.headers) if last is not None else {}
        rows.append(
            {
                "role": call.role,
                "prompt": f"{call.prompt_role or call.role}@{call.prompt_version}",
                "model": settings.model_for_role(call.role),
                "answered_by": last.model if last is not None else None,
                "effort": settings.llm_role_reasoning.get(call.role),
                "status": status,
                "incomplete": last.incomplete if last is not None else None,
                "input_tokens": last.input_tokens if last is not None else 0,
                "output_tokens": last.output_tokens if last is not None else 0,
                "cached_tokens": last.cached_tokens if last is not None else 0,
                "reasoning_tokens": last.reasoning_tokens if last is not None else 0,
                "rate_limit_headers": {
                    k: v for k, v in headers.items() if k.lower() in RATE_LIMIT_HEADERS
                },
                "latency_ms": round((time.monotonic() - started) * 1000),
            }
        )
    return rows


def exit_code(rows: Sequence[Mapping[str, Any]]) -> int:
    return 0 if all(row["status"] == "ok" for row in rows) else 1


async def _main() -> int:
    settings = Settings()
    engine = create_engine(settings, pool_size=2)
    client = ResponsesClient(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    try:
        rows = await run_smoke(settings, create_session_factory(engine), client)
    finally:
        await client.aclose()
        await engine.dispose()
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))
    return exit_code(rows)


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
