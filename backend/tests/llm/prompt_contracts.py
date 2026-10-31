"""`backend/PROMPT_CONTRACTS.md` built from code (ТЗ Фазы 3 §7.6, seam S4).

The registry below is the one place that ties a prompt version to its output schema. It
lives with the tests: `quack.prompts` is a core package and may not import the product
schemas (layer rule `T-A6`). Rebuild the file with
`uv run python -m tests.llm.prompt_contracts`; `test_prompt_contracts_file_is_current`
fails when it is stale.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from quack.artifacts.schemas import ArtifactGenOutput
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.strict_schema import to_strict_json_schema
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.prompts.registry import PromptRegistry
from quack.session.schemas import ExtractorOutputV2, TeacherOutput
from quack.session.turn_policy import MEMORY_UNAVAILABLE_MARKER

CONTRACTS_PATH = Path(__file__).resolve().parents[2] / "PROMPT_CONTRACTS.md"

# Prompt reference → output schema, for the versions of Phase 3.
PROMPT_SCHEMAS: dict[str, type[BaseModel]] = {
    "teacher@1": TeacherOutput,
    "extractor@2": ExtractorOutputV2,
    "evaluator@1": EvaluatorOutput,
    "task_gen@1": TaskGenOutput,
    "structurer@1": StructurerOutput,
    "curator@1": CuratorOutput,
    "diagnostic@1": DiagnosticOutput,
    "artifacts@1": ArtifactGenOutput,
}

# Fields whose values are labels of `LabelMap` (WP14.1, Т-§4.5).
LABEL_FIELDS: dict[str, list[str]] = {
    "teacher@1": [
        "focus_skill_labels",
        "assign_task_label",
        "concept_explained.topic_label",
        "concept_explained.explained_skill_labels",
    ],
    "extractor@2": ["entries.skill", "entries.misconception"],
    "evaluator@1": ["misconception.label"],
    "task_gen@1": ["options.catches", "tests.skill", "variant_of"],
}

MARKERS: dict[str, list[str]] = {"teacher@1": [MEMORY_UNAVAILABLE_MARKER]}

# §7.6 п.2: places that may still change after G1.
EDIT_POINTS = [
    "teacher tool names, if tools appear (R9);",
    "names of label fields (`LABEL_FIELDS`);",
    "`gap_type` values of `extractor@2` (П-6.4);",
    f"the marker of unavailable memory (`{MEMORY_UNAVAILABLE_MARKER}`);",
    "the reply language of the teacher from the profile.",
]

LABEL_RE = re.compile(r"^[STKM][1-9][0-9]*$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_BACKTICKED = re.compile(r"`([^`]+)`")


def _type_text(node: dict[str, Any]) -> str:
    if "enum" in node:
        return " | ".join(
            "null" if v is None else f'"{v}"' if isinstance(v, str) else str(v)
            for v in node["enum"]
        )
    kind = node.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    parts: list[str] = []
    for k in kinds:
        if k == "array":
            parts.append(f"list[{_type_text(node.get('items', {}))}]")
        elif k == "object":
            parts.append("object")
        elif k is not None:
            parts.append(str(k))
    return " | ".join(parts) or "any"


def schema_fields(model: type[BaseModel]) -> list[tuple[str, str]]:
    """`(path, type)` of every property of the strict schema, nested objects included."""
    out: list[tuple[str, str]] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        for name, prop in node.get("properties", {}).items():
            path = f"{prefix}{name}"
            out.append((path, _type_text(prop)))
            target = prop.get("items", prop) if "array" in str(prop.get("type")) else prop
            if "properties" in target:
                walk(target, f"{path}.")

    walk(to_strict_json_schema(model), "")
    return out


def field_names(model: type[BaseModel]) -> set[str]:
    return {path.rsplit(".", 1)[-1] for path, _ in schema_fields(model)}


def output_contract_block(text: str) -> str | None:
    """The text of `## Output contract` up to the next level-2 heading, or `None`."""
    match = re.search(r"^## Output contract\n(.*?)(?=^## |\Z)", text, flags=re.S | re.M)
    return match.group(1) if match else None


def undeclared_names(ref: str, text: str) -> list[str]:
    """Backticked names of the output contract that are neither schema fields nor labels
    (`T-M5`)."""
    block = output_contract_block(text)
    if block is None:
        return []
    allowed = field_names(PROMPT_SCHEMAS[ref])
    return sorted(
        {n for n in _BACKTICKED.findall(block) if n not in allowed and not LABEL_RE.match(n)}
    )


def render_contracts(registry: PromptRegistry | None = None) -> str:
    registry = registry or PromptRegistry()
    lines = [
        "# PROMPT_CONTRACTS",
        "",
        "Generated from code by `uv run python -m tests.llm.prompt_contracts` (ТЗ Фазы 3 §7.6,",
        "seam S4). Do not edit by hand. Prompt texts: `quack/prompts/<role>/v<N>.md`.",
        "",
        "Models see labels instead of ids: skills `S1…`, topics `T1…`, tasks `K1…`,",
        "misconceptions `M1…` (`quack/llm/labels.py`, WP14.1).",
        "",
    ]
    for ref, model in PROMPT_SCHEMAS.items():
        role, version = ref.split("@")
        template = registry.load(role, int(version))
        inputs = list(dict.fromkeys(_PLACEHOLDER.findall(template.text)))
        lines += [f"## `{ref}` → `{model.__name__}`", "", "Output fields:", ""]
        lines += [f"- `{path}`: {kind}" for path, kind in schema_fields(model)]
        lines += ["", "Input blocks: " + (", ".join(f"`{i}`" for i in inputs) or "none")]
        if ref in LABEL_FIELDS:
            lines.append("Label fields: " + ", ".join(f"`{f}`" for f in LABEL_FIELDS[ref]))
        if ref in MARKERS:
            lines.append("Markers: " + ", ".join(f'"{m}"' for m in MARKERS[ref]))
        lines.append("")
    lines += ["## Edit points after G1", ""]
    lines += [f"- {point}" for point in EDIT_POINTS]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    CONTRACTS_PATH.write_text(render_contracts(), encoding="utf-8")
    print(CONTRACTS_PATH)
