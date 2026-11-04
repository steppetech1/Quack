"""`scripts/meta_smoke.py` offline: every role of the table through the gateway on a scripted
provider (ТЗ Фазы 3 WP5.7, ТЗ Фазы 4 WP10.7, DoD п.6a). The provider answers each text role with
an instance built from its own strict schema, the non-text roles and the stream with canned
answers; nothing leaves the process."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel, SecretStr

from quack.config import Settings
from quack.db import Db
from quack.llm.media import ImageResponse, TranscribeResponse
from quack.llm.provider import ProviderResponse
from quack.llm.strict_schema import to_strict_json_schema
from quack.prompts.registry import PromptError, PromptRegistry
from tests.llm.prompt_contracts import ALL_PROMPT_SCHEMAS
from tests.llm.test_phase4_media_roles import PIXEL, SAM_TEXT
from tests.support.llm import ScriptedLLMProvider, ScriptedToolCall, ScriptedToolStep

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "meta_smoke.py"
# Versions of Phase 3 replaced by the role table (D-B-203).
SUPERSEDED = {"teacher@1", "extractor@2"}


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("meta_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["meta_smoke"] = module
    spec.loader.exec_module(module)
    return module


def _sample(node: Any, defs: dict[str, Any]) -> Any:
    """A value valid for a strict JSON Schema node: the first option, the shortest array."""
    if "$ref" in node:
        return _sample(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    if "const" in node:
        return node["const"]
    if "enum" in node:
        return node["enum"][0]
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        return _sample(options[0], defs) if options else None
    kind = node.get("type")
    if isinstance(kind, list):
        kind = next((k for k in kind if k != "null"), "null")
    if kind == "object":
        return {k: _sample(v, defs) for k, v in node.get("properties", {}).items()}
    if kind == "array":
        count = node.get("minItems", 0)
        return [_sample(node["items"], defs) for _ in range(count)]
    if kind == "string":
        return "x" * max(1, node.get("minLength", 1))
    if kind == "integer":
        return max(0, node.get("minimum", 0))
    if kind == "number":
        return float(max(0, node.get("minimum", 0)))
    if kind == "boolean":
        return True
    return None


def _instance(model: type[BaseModel]) -> BaseModel:
    schema = to_strict_json_schema(model)
    return model.model_validate(_sample(schema, schema.get("$defs", {})))


@dataclass
class MetaScripted(ScriptedLLMProvider):
    """The scripted provider with the three non-text roles and the answers of the smoke."""

    def __post_init__(self) -> None:
        super().__post_init__()
        smoke = _script()
        models = {*smoke.ROLE_SCHEMAS.values(), *(m for _, _, m, _, _ in smoke.LEGACY_CALLS)}
        for model in models:
            if model.__name__ not in self.responders:
                self.responders[model.__name__] = lambda _r, model=model: _instance(model)
        self.respond(smoke.ProbeOutput.__name__, lambda _r: {"value": smoke.PROBE_VALUE})
        self.script_tools(
            smoke.ProbeOutput.__name__,
            ScriptedToolStep((ScriptedToolCall(smoke.PROBE_TOOL, {"key": "quack"}),)),
        )

    async def segment(self, request: Any) -> ProviderResponse:
        return ProviderResponse(SAM_TEXT, 300, 12, 0, request.model)

    async def transcribe(self, request: Any) -> TranscribeResponse:
        return TranscribeResponse("минус b", 0, 0, 0, request.model, audio_duration_ms=500)

    async def illustrate(self, request: Any) -> ImageResponse:
        return ImageResponse("", 0, 0, 0, request.model, image=PIXEL.encode())


def test_smoke_calls_follow_the_role_table_and_contracts() -> None:
    """Each text call takes the prompt of its table row and an output schema of that prompt;
    every prompt of the registry is covered but the versions replaced by the table."""
    smoke = _script()
    settings = Settings(env="test")
    calls = smoke.smoke_calls(settings)
    refs: set[str] = set()
    for call in calls:
        ref = f"{call.prompt_role or call.role}@{call.prompt_version}"
        refs.add(ref)
        assert call.response_model in ALL_PROMPT_SCHEMAS[ref], call.role
        if call.role in settings.llm_roles:
            assert settings.llm_roles[call.role].prompt == ref
    assert refs >= set(ALL_PROMPT_SCHEMAS) - SUPERSEDED
    assert {"teacher", "analyst_deep", "extractor"} <= {c.role for c in calls}


async def test_smoke_runs_every_role_through_the_gateway(settings: Settings, pg_db: Db) -> None:
    """All 26 roles of the table, the Phase 3 prompts and the stream with a tool pass; a role
    without a prompt is `skipped`; the table and the report carry model, status and latency."""
    smoke = _script()
    provider = MetaScripted()
    rows = await smoke.run_smoke(settings, pg_db, provider)
    by_role = {row["role"]: row for row in rows}
    assert set(settings.llm_roles) <= set(by_role) and len(settings.llm_roles) == 26
    registry = PromptRegistry()
    for role, row in settings.llm_roles.items():
        status = by_role[role]["status"]
        if row.prompt is None:
            assert status == "ok", by_role[role]  # segmenter, voice, illustrator
            continue
        name, version = row.prompt.rsplit("@", 1)
        try:
            registry.load(name, int(version))
        except PromptError:
            assert status.startswith("skipped"), role
        else:
            assert status == "ok", by_role[role]
    assert {r["role"] for r in rows if r["status"].startswith("skipped")} <= {
        "material_parser",
        "viz_checker",
    }
    for role in smoke.MEDIA_ROLES:
        assert by_role[role]["model"] == settings.model_for_role(role)
    stream = by_role[smoke.STREAM_ROW]
    assert stream["status"] == "ok" and stream["tools_called"] == 1 and stream["steps"] == 2
    assert len(provider.stream_calls) == 2 and provider.stream_calls[0].tools
    assert {"evaluator", "task_gen", "structurer", "curator", "diagnostic", "artifacts"} <= set(
        by_role
    )
    for row in rows:
        assert set(row) >= {
            "role",
            "prompt",
            "model",
            "effort",
            "status",
            "input_tokens",
            "output_tokens",
            "cached_tokens",
            "reasoning_tokens",
            "rate_limit_headers",
            "latency_ms",
        }
    assert smoke.exit_code(rows) == 0
    assert smoke.exit_code([*rows, {**rows[0], "status": "LLMSchemaError:schema"}]) == 1
    report = smoke.render_report(rows)
    assert "latency_ms" in report and "Не проверено (нет промпта" in report
    for role in settings.llm_roles:
        assert role in report


async def test_smoke_reports_a_failed_role(settings: Settings, pg_db: Db) -> None:
    """A role whose answer breaks the schema is a failed row and exit code 1; the rest go on."""
    smoke = _script()
    provider = MetaScripted()
    provider.respond("MisconceptionDescription", lambda _r: "{not json")
    rows = await smoke.run_smoke(settings, pg_db, provider)
    failed = [r["role"] for r in rows if r["status"].split(":")[0] not in ("ok", "skipped")]
    assert failed == ["misconception_describer"]
    assert smoke.exit_code(rows) == 1


async def test_smoke_without_key_is_not_checked() -> None:
    """No `META_API_KEY`: no call, the report says «Не проверено» with the command, exit 1."""
    smoke = _script()
    code, report = await smoke.run(Settings(env="test", meta_api_key=SecretStr("")))
    assert code == 1
    assert report.startswith("Не проверено") and "scripts/meta_smoke.py" in report
    assert "META_API_KEY=" in report
