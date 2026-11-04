"""Live smoke of the Meta API: one call of every role through the gateway (ТЗ Фазы 3 WP5.7,
ТЗ Фазы 4 WP10.7, DoD п.6a, Т-§11.8 п.1). Not part of `pytest` or CI.

Covers:
- every role of `Settings.llm_roles` — a text role is one structured call by its own output
  schema through `LLMGateway.generate_structured` (strict schema, effort, output limit, usage
  row); `segmenter`, `voice` and `illustrator` go through `LLMGateway.segment`,
  `transcribe` and `illustrate` on a tiny built-in input (D-B-187);
- the prompts of Phase 3 that are not roles of the table (`evaluator`, `task_gen`, `structurer`,
  `curator`, `diagnostic`, `artifacts`);
- the streamed structured answer after a tool result: `stream_structured` inside
  `run_tool_loop` on the model of the teacher (Т-§11.8 п.1).

A recording wrapper keeps the raw answer to report the token kinds and the rate-limit headers.
A role whose prompt is not in the registry yet (`viz_checker`) is reported as
`skipped`. Output: the table «role → status, model, latency» and one line of JSON per row;
`--out FILE` writes the same to a file. Exit code 1 if any role failed. Without `META_API_KEY`
nothing is called: the report says «Не проверено» with the command, exit code 1.

Run from `backend/` against a migrated Postgres (rows go to `ops.llm_usage` under a
fresh learner id):

    META_API_KEY=... META_API_BASE_URL=https://api.meta.ai/v1 POSTGRES_DSN=... \\
        uv run python scripts/meta_smoke.py --out /tmp/meta_smoke.txt
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import math
import re
import struct
import sys
import time
import wave
import zlib
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quack.agents import schemas as agents
from quack.artifacts.schemas import ArtifactGenOutput
from quack.artifacts.writer import ArtifactWriterOutput
from quack.canvas.diagnosis import CanvasDiagnoserOutput
from quack.canvas.recognition import RecognizerOutput
from quack.config import Settings, default_llm_roles
from quack.db import Db, open_db
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.errors import LLMError, ProviderIncompleteError
from quack.llm.gateway import LLMGateway
from quack.llm.provider import (
    RATE_LIMIT_HEADERS,
    ImageInput,
    LLMProvider,
    ProviderRequest,
    ProviderResponse,
)
from quack.llm.responses_client import ResponsesClient
from quack.llm.streaming import StreamEvent, StreamRequest
from quack.llm.strict_schema import to_strict_json_schema
from quack.llm.tool_loop import run_tool_loop
from quack.materials.parse import MaterialParserOutput
from quack.practice import role_outputs
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.prompts.registry import PromptError, PromptRegistry, PromptTemplate
from quack.session.teacher_output import TeacherOutputV2

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")

LIVE_COMMAND = (
    "META_API_KEY=... META_API_BASE_URL=https://api.meta.ai/v1 POSTGRES_DSN=... "
    "uv run python scripts/meta_smoke.py --out /tmp/meta_smoke.txt"
)
NOT_CHECKED = (
    "Не проверено: нет META_API_KEY, живой прогон не выполнен. "
    "Команда запуска (из backend/, при поднятом Postgres с миграциями):"
)

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


# Output schema of each text role of the table (ТЗ Ф4 §6.6, `PROMPT_CONTRACTS.md`). A prompt with
# two schemas (`builder@1`, `recognizer@1`) is smoked by its first one. `viz_checker` has no
# prompt in the registry yet, so it is reported as `skipped`.
ROLE_SCHEMAS: dict[str, type[BaseModel]] = {
    "teacher": TeacherOutputV2,
    "teacher_fallback": TeacherOutputV2,
    "extractor": agents.ExtractorOutputV3,
    "verdict_free": role_outputs.ModelVerdictOutput,
    "verdict_proof": role_outputs.ModelVerdictOutput,
    "second_opinion": role_outputs.ModelVerdictOutput,
    "canvas_diagnoser": CanvasDiagnoserOutput,
    "recognizer": RecognizerOutput,
    "analyst": agents.AnalystOutput,
    "analyst_deep": agents.AnalystOutput,
    "builder": agents.BuilderOutput,
    "task_generator": role_outputs.TaskGeneratorOutput,
    "task_checker": role_outputs.TaskCheckOutput,
    "task_arbiter": role_outputs.TaskCheckOutput,
    "solution_space": role_outputs.SolutionSpace,
    "viz_builder": agents.VizBuilderOutput,
    "misconception_describer": agents.MisconceptionDescription,
    "artifact_writer": ArtifactWriterOutput,
    "material_parser": MaterialParserOutput,
    "today_planner": agents.TodayPlannerOutput,
    "domain_classifier": agents.DomainClassifierOutput,
    "release_judge": agents.ReleaseJudgeOutput,
}
# The roles that are not text calls (D-B-187).
MEDIA_ROLES = ("segmenter", "voice", "illustrator")
# Roles that read an image (Т-§11.1 «Spark с изображением»).
IMAGE_ROLES = frozenset({"recognizer", "verdict_free"})
# The prompts of Phase 3 that are not roles of the table; `teacher@1`, `extractor@2` are
# replaced by `teacher@2`, `extractor@3` (D-B-203).
LEGACY_CALLS: tuple[tuple[str, int, type[BaseModel], str | None, dict[str, Any]], ...] = (
    ("evaluator", 1, EvaluatorOutput, None, {}),
    ("task_gen", 1, TaskGenOutput, None, {}),
    ("structurer", 1, StructurerOutput, None, {}),
    ("curator", 1, CuratorOutput, None, {}),
    ("diagnostic", 1, DiagnosticOutput, None, {"mode": "generate"}),
    ("artifacts", 1, ArtifactGenOutput, None, {"mode": "digest"}),
)
STREAM_ROW = "stream_tool"
_ROLE_OVERRIDES: dict[str, dict[str, Any]] = {
    "builder": {"mode": "Reconcile"},
    "recognizer": {"mode": "line"},
    "teacher": {"mode": "learn"},
}


@dataclass(frozen=True)
class SmokeCall:
    role: str
    prompt_version: int
    response_model: type[BaseModel]
    prompt_role: str | None = None
    overrides: Mapping[str, Any] = field(default_factory=dict)


def smoke_calls(settings: Settings | None = None) -> list[SmokeCall]:
    """The structured calls: one per text role of the table that has a schema here, with the
    prompt of its row (`teacher@2`, `analyst@1` for `analyst_deep`, ...), then the prompts of
    Phase 3 outside the table."""
    roles = settings.llm_roles if settings is not None else default_llm_roles()
    calls: list[SmokeCall] = []
    for role, row in roles.items():
        if row.prompt is None or role not in ROLE_SCHEMAS:
            continue
        name, version = row.prompt.rsplit("@", 1)
        calls.append(
            SmokeCall(
                role,
                int(version),
                ROLE_SCHEMAS[role],
                prompt_role=None if name == role else name,
                overrides=_ROLE_OVERRIDES.get(role, {}),
            )
        )
    calls.extend(
        SmokeCall(role, version, model, prompt_role, overrides)
        for role, version, model, prompt_role, overrides in LEGACY_CALLS
    )
    return calls


def _variables(call: SmokeCall, registry: PromptRegistry) -> dict[str, Any]:
    template = registry.load(call.prompt_role or call.role, call.prompt_version)
    names = dict.fromkeys(_PLACEHOLDER.findall(template.text))
    values = {**_COMMON, **call.overrides}
    return {name: values.get(name, "none") for name in names}


# --- tiny built-in inputs of the non-text roles and of the image roles ---


def _png(size: int = 64) -> bytes:
    """A grayscale PNG with a dark disc on white: input of `segmenter` and the image roles."""
    rows = bytearray()
    centre, radius = size / 2, size / 4
    for y in range(size):
        rows.append(0)
        for x in range(size):
            inside = math.hypot(x - centre, y - centre) <= radius
            rows.append(40 if inside else 255)

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )


def _wav(seconds: float = 0.5, rate: int = 16_000) -> bytes:
    """A mono 16-bit PCM WAV with a quiet tone: input of `voice`."""
    frames = b"".join(
        struct.pack("<h", int(2000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(int(seconds * rate))
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(frames)
    return out.getvalue()


# --- the probe of the streamed answer after a tool result (Т-§11.8 п.1) ---

PROBE_TOOL = "smoke_lookup"
PROBE_VALUE = 42
# Text of the probe call: input of the smoke check, not a prompt of a role.
PROBE_PROMPT = (
    f'Tool loop check. Call the function {PROBE_TOOL} once with key "quack". Do not answer '
    "before its result arrives. Then answer with one JSON object with the field value: the "
    "number the function returned."
)


class ProbeArgs(BaseModel):
    key: str


class ProbeOutput(BaseModel):
    value: int


def probe_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": PROBE_TOOL,
        "description": "Returns the number registered under the key.",
        "parameters": to_strict_json_schema(ProbeArgs),
        "strict": True,
    }


@dataclass
class _Seen:
    """What the recorder keeps of the last answer: the model, tokens by kind, headers and the
    effort and output limit the request carried."""

    model: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reasoning_tokens: int
    incomplete: bool
    headers: Mapping[str, str]
    effort: str | None
    max_output_tokens: int | None


def _seen(response: ProviderResponse, effort: str | None, limit: int | None) -> _Seen:
    return _Seen(
        response.model,
        response.input_tokens,
        response.output_tokens,
        response.cached_tokens,
        response.reasoning_tokens,
        response.incomplete,
        response.headers,
        effort,
        limit,
    )


@dataclass
class _Recorder:
    """The provider of the gateway that keeps the last raw answer for the report; the media
    methods and the stream are passed to the real provider unchanged."""

    provider: Any
    last: _Seen | None = None

    async def complete_structured(self, request: ProviderRequest) -> ProviderResponse:
        self.last = None
        response: ProviderResponse = await self.provider.complete_structured(request)
        self.last = _seen(response, request.reasoning_effort, request.max_output_tokens)
        return response

    async def segment(self, request: Any) -> ProviderResponse:
        self.last = None
        response: ProviderResponse = await self.provider.segment(request)
        self.last = _seen(response, None, None)
        return response

    async def transcribe(self, request: Any) -> ProviderResponse:
        self.last = None
        response: ProviderResponse = await self.provider.transcribe(request)
        self.last = _seen(response, None, None)
        return response

    async def illustrate(self, request: Any) -> ProviderResponse:
        self.last = None
        response: ProviderResponse = await self.provider.illustrate(request)
        self.last = _seen(response, None, None)
        return response

    def stream_structured(self, request: StreamRequest) -> AsyncIterator[StreamEvent]:
        stream: AsyncIterator[StreamEvent] = self.provider.stream_structured(request)
        return stream


def _row(
    role: str,
    prompt: str | None,
    settings: Settings,
    status: str,
    seen: _Seen | None,
    started: float,
    **extra: Any,
) -> dict[str, Any]:
    headers = dict(seen.headers) if seen is not None else {}
    effort = settings.effort_for_role(role)
    return {
        "role": role,
        "prompt": prompt,
        "model": settings.model_for_role(role),
        "answered_by": seen.model if seen is not None else None,
        "effort": effort,
        "sent_effort": seen.effort if seen is not None else None,
        "sent_max_output_tokens": seen.max_output_tokens if seen is not None else None,
        "status": status,
        "incomplete": seen.incomplete if seen is not None else None,
        "input_tokens": seen.input_tokens if seen is not None else 0,
        "output_tokens": seen.output_tokens if seen is not None else 0,
        "cached_tokens": seen.cached_tokens if seen is not None else 0,
        "reasoning_tokens": seen.reasoning_tokens if seen is not None else 0,
        "rate_limit_headers": {k: v for k, v in headers.items() if k.lower() in RATE_LIMIT_HEADERS},
        "latency_ms": round((time.monotonic() - started) * 1000),
        **extra,
    }


def _failure(exc: Exception) -> str:
    return f"{type(exc).__name__}:{getattr(exc, 'reason', '')}"


async def run_smoke(
    settings: Settings, db: Db, provider: LLMProvider, only: frozenset[str] = frozenset()
) -> list[dict[str, Any]]:
    """Run every smoke call once; one report row per role (the table order), then the Phase 3
    prompts and the stream probe. `only` — just these rows (a re-check of a failed one)."""
    recorder = _Recorder(provider)
    gateway = LLMGateway(recorder, settings, db=db)
    registry = PromptRegistry()
    learner, run = uuid4(), uuid4().hex[:8]
    calls = {call.role: call for call in smoke_calls(settings)}
    rows: list[dict[str, Any]] = []

    def wanted(name: str) -> bool:
        return not only or name in only

    async def structured(call: SmokeCall) -> dict[str, Any]:
        started = time.monotonic()
        status = "ok"
        prompt = f"{call.prompt_role or call.role}@{call.prompt_version}"
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
                images=[ImageInput("image/png", _png())] if call.role in IMAGE_ROLES else (),
            )
        except (LLMError, ProviderIncompleteError) as exc:
            status = _failure(exc)
        return _row(call.role, prompt, settings, status, recorder.last, started)

    async def media(role: str) -> dict[str, Any]:
        started = time.monotonic()
        status = "ok"
        try:
            if role == "segmenter":
                await gateway.segment(
                    ImageInput("image/png", _png()), learner_id=learner, concept="circle"
                )
            elif role == "voice":
                await gateway.transcribe(_wav(), learner_id=learner)
            else:
                await gateway.illustrate(
                    "A simple blue circle on a white background", learner_id=learner
                )
        except (LLMError, ProviderIncompleteError, ValidationError) as exc:
            status = _failure(exc)
        return _row(role, None, settings, status, recorder.last, started)

    for role, table_row in settings.llm_roles.items():
        if not wanted(role):
            continue
        if role in MEDIA_ROLES:
            rows.append(await media(role))
        elif role in calls:
            rows.append(await structured(calls[role]))
        else:
            reason = "no_schema"
            if table_row.prompt is not None:
                name, version = table_row.prompt.rsplit("@", 1)
                try:
                    registry.load(name, int(version))
                except PromptError:
                    reason = "no_prompt"
            rows.append(
                _row(role, table_row.prompt, settings, f"skipped:{reason}", None, time.monotonic())
            )
    for call in calls.values():
        if call.role not in settings.llm_roles and wanted(call.role):
            rows.append(await structured(call))
    if wanted(STREAM_ROW):
        rows.append(await stream_probe(gateway, settings, learner))
    return rows


async def stream_probe(gateway: LLMGateway, settings: Settings, learner: UUID) -> dict[str, Any]:
    """`stream_structured` inside `run_tool_loop` on the model of the teacher: the model calls
    the tool, gets its result, and the final answer streams by the strict schema (Т-§11.8 п.1)."""
    started = time.monotonic()
    row = settings.llm_roles["teacher"]
    request = StreamRequest(
        ProviderRequest(
            model=settings.model_for_role("teacher"),
            prompt=PROBE_PROMPT,
            json_schema=to_strict_json_schema(ProbeOutput),
            schema_name=ProbeOutput.__name__,
            temperature=0.0,
            timeout_sec=row.timeout_s,
            reasoning_effort="low",
            max_output_tokens=row.max_output_tokens,
        ),
        tools=(probe_tool(),),
    )
    template = PromptTemplate("teacher", 0, PROBE_PROMPT, "")
    provider = gateway.streaming(
        "teacher", template, learner_id=learner, session_id=None, turn_id=None
    )
    called: list[str] = []

    async def execute(name: str, arguments: str) -> str:
        called.append(name)
        return str(PROBE_VALUE) if name == PROBE_TOOL else f"error: unknown tool {name}"

    status, steps, seen = "ok", 0, None
    try:
        result = await run_tool_loop(
            provider,
            request,
            execute,
            tool_timeout_ms=settings.tool_timeout_ms,
            max_steps=settings.tool_loop_max_steps,
            max_output_tokens_cap=settings.max_output_tokens_cap,
        )
        steps = result.steps
        usage = result.usage
        seen = _Seen(
            result.model,
            usage.input_tokens,
            usage.output_tokens,
            usage.cached_tokens,
            usage.reasoning_tokens,
            False,
            result.headers,
            request.base.reasoning_effort,
            request.base.max_output_tokens,
        )
        value = ProbeOutput.model_validate_json(result.text).value
        if not called:
            status = "NoToolCall:"
        elif value != PROBE_VALUE:
            status = "WrongToolResult:"
    except Exception as exc:  # a live smoke reports a broken probe, never aborts
        status = _failure(exc)
    return _row(
        STREAM_ROW,
        "stream+tool",
        settings,
        status,
        seen,
        started,
        steps=steps,
        tools_called=len(called),
    )


def exit_code(rows: Sequence[Mapping[str, Any]]) -> int:
    """0 when every row is `ok` or `skipped` (no prompt yet); 1 when any role failed."""
    return 0 if all(str(row["status"]).split(":")[0] in ("ok", "skipped") for row in rows) else 1


def render_table(rows: Sequence[Mapping[str, Any]]) -> str:
    """The report table «role → status, model, latency»."""
    header = ("role", "status", "model", "latency_ms")
    lines = [header, *((r["role"], r["status"], r["model"], str(r["latency_ms"])) for r in rows)]
    widths = [max(len(line[i]) for line in lines) for i in range(len(header))]
    out = [
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True)).rstrip()
        for line in lines
    ]
    out.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(out)


def render_report(rows: Sequence[Mapping[str, Any]]) -> str:
    skipped = [r["role"] for r in rows if str(r["status"]).startswith("skipped")]
    lines = [render_table(rows), ""]
    if skipped:
        lines.append(f"Не проверено (нет промпта или схемы в реестре): {', '.join(skipped)}")
        lines.append("")
    lines.extend(json.dumps(row, ensure_ascii=False) for row in rows)
    return "\n".join(lines)


def unchecked_report() -> str:
    return f"{NOT_CHECKED}\n\n    {LIVE_COMMAND}"


async def run(
    settings: Settings | None = None, only: frozenset[str] = frozenset()
) -> tuple[int, str]:
    """The whole run: the report text and the exit code. Without `META_API_KEY` nothing is
    called — the report is «Не проверено» with the command."""
    settings = settings or Settings()
    if not settings.meta_api_key.get_secret_value():
        return 1, unchecked_report()
    db = await open_db(settings)
    client = ResponsesClient(settings.meta_api_base_url, settings.meta_api_key.get_secret_value())
    try:
        rows = await run_smoke(settings, db, client, only)
    finally:
        await client.aclose()
        await db.close()
    return exit_code(rows), render_report(rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live smoke of the Meta API (WP10.7).")
    parser.add_argument("--out", type=Path, default=None, help="also write the report to a file")
    parser.add_argument("--only", nargs="*", default=[], help="just these rows")
    args = parser.parse_args(argv)
    code, report = asyncio.run(run(only=frozenset(args.only)))
    print(report)
    if args.out is not None:
        args.out.write_text(report + "\n", encoding="utf-8")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
