"""`scripts/meta_smoke.py` offline: the same calls through the gateway on `ScriptedLLMProvider`
(ТЗ Фазы 3 WP5.7, DoD п.6a)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from quack.config import Settings
from quack.db.postgres import SessionFactory
from tests.llm.prompt_contracts import PROMPT_SCHEMAS
from tests.support.llm import ScriptedLLMProvider

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "meta_smoke.py"


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("meta_smoke", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["meta_smoke"] = module
    spec.loader.exec_module(module)
    return module


def test_smoke_covers_every_phase3_prompt() -> None:
    """Every prompt of Phase 3 (and `teacher_fallback`) has one call with its schema."""
    smoke = _script()
    calls = smoke.smoke_calls()
    refs = {f"{c.prompt_role or c.role}@{c.prompt_version}" for c in calls}
    assert refs == set(PROMPT_SCHEMAS)
    assert {c.role for c in calls} >= {"teacher", "teacher_fallback"}
    for call in calls:
        prompt_ref = f"{call.prompt_role or call.role}@{call.prompt_version}"
        assert call.response_model is PROMPT_SCHEMAS[prompt_ref], call.role


async def test_smoke_runs_through_the_gateway(settings: Settings, db: SessionFactory) -> None:
    """Each call goes through the gateway; the report has the model, `effort`, status, token
    kinds, rate-limit headers and latency; every answer passed its schema."""
    smoke = _script()
    provider = ScriptedLLMProvider()
    rows = await smoke.run_smoke(settings, db, provider)
    assert len(rows) == len(smoke.smoke_calls()) == len(provider.calls)
    for row in rows:
        assert row["status"] == "ok", row
        assert row["model"] == settings.model_for_role(row["role"])
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
