"""`backend/PROMPT_CONTRACTS.md` built from code (ТЗ Фазы 3 §7.6, seam S4; ТЗ Ф4 §7.6, S8).

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

from quack.agents import schemas as agents
from quack.artifacts.schemas import ArtifactGenOutput
from quack.domains.schemas import CuratorOutput, DiagnosticOutput, StructurerOutput
from quack.llm.labels import LABEL_PREFIX
from quack.llm.strict_schema import to_strict_json_schema
from quack.practice import role_outputs
from quack.practice.schemas import EvaluatorOutput, TaskGenOutput
from quack.prompts.registry import PromptRegistry
from quack.session.schemas import ExtractorOutputV2, TeacherOutput
from quack.session.teacher_output import TeacherOutputV2
from quack.session.turn_policy import MEMORY_UNAVAILABLE_MARKER

CONTRACTS_PATH = Path(__file__).resolve().parents[2] / "PROMPT_CONTRACTS.md"

# Prompt reference → output schema, for the versions of Phase 3 (`meta_smoke.py` covers
# exactly these until WP10.7).
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

# The roles of stream B in Phase 4 (ТЗ Ф4 §6.6). A prompt with two modes has one schema per
# mode (`builder@1`: reconciliation and hygiene, D-B-127). The roles whose layer 2 stream C
# writes (§7.3 п.2) are added with their prompts.
PHASE4_PROMPT_SCHEMAS: dict[str, tuple[type[BaseModel], ...]] = {
    "teacher@2": (TeacherOutputV2,),
    "extractor@3": (agents.ExtractorOutputV3,),
    "verdict_free@1": (role_outputs.ModelVerdictOutput,),
    "verdict_proof@1": (role_outputs.ModelVerdictOutput,),
    "second_opinion@1": (role_outputs.ModelVerdictOutput,),
    "task_generator@1": (role_outputs.TaskGeneratorOutput,),
    "task_checker@1": (role_outputs.TaskCheckOutput,),
    "task_arbiter@1": (role_outputs.TaskCheckOutput,),
    "solution_space@1": (role_outputs.SolutionSpace,),
    "analyst@1": (agents.AnalystOutput,),
    "builder@1": (agents.BuilderOutput, agents.HygieneOutput),
    "misconception_describer@1": (agents.MisconceptionDescription,),
    "today_planner@1": (agents.TodayPlannerOutput,),
    "domain_classifier@1": (agents.DomainClassifierOutput,),
    "viz_builder@1": (agents.VizBuilderOutput,),
    "release_judge@1": (agents.ReleaseJudgeOutput,),
}

# Every prompt with its schemas: what `T-M5` and `PROMPT_CONTRACTS.md` cover.
ALL_PROMPT_SCHEMAS: dict[str, tuple[type[BaseModel], ...]] = {
    **{ref: (model,) for ref, model in PROMPT_SCHEMAS.items()},
    **PHASE4_PROMPT_SCHEMAS,
}

# Prompts that run on a stub until the owner's doctrine arrives (ТЗ Ф4 §0.4.3, §7.6 п.5):
# reference → BLOCKED number of `PHASE4_NOTES.md` §«Заблокировано».
DOCTRINE_PENDING_MARKER = "Doctrine for this role is pending from the owner"
DOCTRINE_PENDING: dict[str, str] = {
    "task_checker@1": "BLOCKED-B-101",
    "task_arbiter@1": "BLOCKED-B-102",
    "solution_space@1": "BLOCKED-B-103",
    "misconception_describer@1": "BLOCKED-B-104",
    "today_planner@1": "BLOCKED-B-105",
    "domain_classifier@1": "BLOCKED-B-106",
    "viz_builder@1": "BLOCKED-B-107",
    "release_judge@1": "BLOCKED-B-108",
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
    "teacher@2": [
        "p1_head.targetSkills",
        "p3_markup.tasks.tests.skill",
        "p3_markup.tasks.variantOf",
        "p3_markup.tasks.options.misconception",
        "p3_markup.tasks.pairTaskId",
        "p3_markup.exposures.skill",
        "p3_markup.targetsMisconception",
        "p3_markup.planUpdate.cancel_requests",
        "p3_markup.agentRequests.params.skill",
        "p3_markup.agentRequests.params.topic",
        "p3_markup.agentRequests.params.task",
        "p3_markup.agentRequests.params.misconception",
        "p3_markup.deadline.hub_label",
        "p3_markup.deadline.coverage",
    ],
    "extractor@3": ["elements.skill", "elements.misconception", "elements.root_cause.skill"],
    "verdict_free@1": ["misconception"],
    "verdict_proof@1": ["misconception"],
    "second_opinion@1": ["misconception"],
    "task_generator@1": [
        "tasks.markup.tests.skill",
        "tasks.markup.variantOf",
        "tasks.markup.options.misconception",
    ],
    "solution_space@1": ["wrong_branches.misconception"],
    "analyst@1": [
        "frontier.skill",
        "gap_hypotheses.skill",
        "forecast.skill",
        "forecast.misconception",
    ],
    "builder@1": ["items.target", "items.same_idea", "pairs.first", "pairs.second"],
    "today_planner@1": ["items.tasks"],
}

MARKERS: dict[str, list[str]] = {
    "teacher@1": [MEMORY_UNAVAILABLE_MARKER],
    "teacher@2": [MEMORY_UNAVAILABLE_MARKER],
}

# §7.6 п.2: places that may still change after G1.
EDIT_POINTS = [
    "teacher tool names, if tools appear (R9);",
    "names of label fields (`LABEL_FIELDS`);",
    "`gap_type` values of `extractor@2` (П-6.4);",
    f"the marker of unavailable memory (`{MEMORY_UNAVAILABLE_MARKER}`);",
    "the reply language of the teacher from the profile.",
    "Phase 4: `intent` and `approach` lists of `teacher@2` (D-B-104, owner's doctrine);",
    "Phase 4: names of the input blocks of the Phase 4 roles, until their context builders"
    " (WP11.6, WP12.4–12.6, WP13–WP16) are written (D-B-125);",
    "Phase 4: the domain label of `domain_classifier@1` (no `LabelMap` kind yet, WP16.4);",
    "Phase 4: layer 1 of the roles in `DOCTRINE_PENDING` (owner's doctrine).",
]

LABEL_RE = re.compile(rf"^[{''.join(LABEL_PREFIX.values())}][1-9][0-9]*$")
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
    allowed = set().union(*(field_names(model) for model in ALL_PROMPT_SCHEMAS[ref]))
    return sorted(
        {n for n in _BACKTICKED.findall(block) if n not in allowed and not LABEL_RE.match(n)}
    )


def render_contracts(registry: PromptRegistry | None = None) -> str:
    registry = registry or PromptRegistry()
    lines = [
        "# PROMPT_CONTRACTS",
        "",
        "Generated from code by `uv run python -m tests.llm.prompt_contracts` (ТЗ Фазы 3 §7.6,",
        "seam S4; ТЗ Ф4 §7.6, seam S8). Do not edit by hand. Prompt texts:",
        "`quack/prompts/<role>/v<N>.md`.",
        "",
        "Models see labels instead of ids: skills `S1…`, topics `T1…`, tasks `K1…`,",
        "misconceptions `M1…`; in Phase 4 also hubs `H1…`, material chunks `P1…`, cards `C1…`,",
        "agent requests `R1…`, evidence `E1…`, canvas lines `L1…` (`quack/llm/labels.py`,",
        "WP14.1, D-B-108).",
        "",
    ]
    for ref, models in ALL_PROMPT_SCHEMAS.items():
        role, version = ref.split("@")
        template = registry.load(role, int(version))
        inputs = list(dict.fromkeys(_PLACEHOLDER.findall(template.text)))
        names = ", ".join(f"`{model.__name__}`" for model in models)
        lines += [f"## `{ref}` → {names}", ""]
        for model in models:
            title = f"Output fields of `{model.__name__}`:" if len(models) > 1 else "Output fields:"
            lines += [title, ""]
            lines += [f"- `{path}`: {kind}" for path, kind in schema_fields(model)]
            lines.append("")
        lines += ["Input blocks: " + (", ".join(f"`{i}`" for i in inputs) or "none")]
        if ref in LABEL_FIELDS:
            lines.append("Label fields: " + ", ".join(f"`{f}`" for f in LABEL_FIELDS[ref]))
        if ref in MARKERS:
            lines.append("Markers: " + ", ".join(f'"{m}"' for m in MARKERS[ref]))
        if ref in DOCTRINE_PENDING:
            lines.append(f"Layer 1: stub, owner's doctrine pending ({DOCTRINE_PENDING[ref]})")
        lines.append("")
    lines += ["## Edit points after G1", ""]
    lines += [f"- {point}" for point in EDIT_POINTS]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    CONTRACTS_PATH.write_text(render_contracts(), encoding="utf-8")
    print(CONTRACTS_PATH)
