"""Memory tools of the teacher (Т-§10.4; ТЗ Ф4 WP14.4, seam S11 / S12).

Six functions passed to the model with `tool_choice: auto` (Т-§11.5). Arguments and
results use short labels (Т-§4.5): skills `S…`, tasks `K…`, evidence `E…`, canvas lines
`L…` (D-B-108). Results are text and count against the context budget; their row limits
are `Settings.memory_tool_limits` (D-B-103). With Modal down, `search_materials` and
`similar_mistakes(text)` return `SEARCH_UNAVAILABLE` (Т-§11.9, ТЗ Ф4 §4.4).
Agent requests are not a tool: they are `agentRequests` of the turn output (Т-§10.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.llm.strict_schema import to_strict_json_schema

type ToolName = Literal[
    "skill_history",
    "similar_mistakes",
    "past_solution",
    "voice_at_line",
    "search_materials",
    "prerequisites",
]

# Т-§10.4, ТЗ Ф4 §4.4: what a search tool returns while Modal is unavailable.
SEARCH_UNAVAILABLE = "search temporarily unavailable"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SkillHistoryArgs(_Args):
    """Evidence of a skill in words: task, outcome, help, quote, date."""

    skill: str
    level: Annotated[int, Field(ge=1, le=6)] | None


class SimilarMistakesArgs(_Args):
    """Similar past mistakes, other domains included: by an evidence label or by a text;
    exactly one is set."""

    evidence: str | None
    text: str | None


class PastSolutionArgs(_Args):
    """Past solutions of the learner (canvas lines and answers): by a task or a skill label;
    exactly one is set."""

    task: str | None
    skill: str | None


class VoiceAtLineArgs(_Args):
    """What the learner said while writing a canvas line."""

    line: str


class SearchMaterialsArgs(_Args):
    """Material chunks with a page or slide reference; `skills` raise chunks that teach them."""

    query: str
    skills: list[str]


class PrerequisitesArgs(_Args):
    """Prerequisites of a skill with their state."""

    skill: str


@dataclass(frozen=True)
class ToolSpec:
    name: ToolName
    description: str
    args: type[_Args]


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        "skill_history",
        "Evidence of a skill in words: task, outcome, help, quote, date.",
        SkillHistoryArgs,
    ),
    ToolSpec(
        "similar_mistakes",
        "Similar past mistakes of the learner, also from other domains.",
        SimilarMistakesArgs,
    ),
    ToolSpec(
        "past_solution",
        "Past solutions of the learner: canvas lines and answers.",
        PastSolutionArgs,
    ),
    ToolSpec(
        "voice_at_line",
        "What the learner said while writing a canvas line.",
        VoiceAtLineArgs,
    ),
    ToolSpec(
        "search_materials",
        "Chunks of the learner's materials with a page or slide reference.",
        SearchMaterialsArgs,
    ),
    ToolSpec(
        "prerequisites",
        "Prerequisites of a skill with their state.",
        PrerequisitesArgs,
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}


def tool_definition(tool: ToolSpec) -> dict[str, Any]:
    """The Responses API function of `tool` (Т-§11.5): strict JSON Schema of its arguments."""
    return {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": to_strict_json_schema(tool.args),
        "strict": True,
    }


class ToolContext:
    """What a tool call reads with: learner, session, label table, read session of Neo4j,
    the embedding client (seam S12). Filled in WP14.4."""


async def run_tool(name: str, arguments: str, ctx: ToolContext) -> str:
    """Execute one tool call: parse `arguments` (JSON) by the tool's model, read memory
    through `quack.graph.read` (seam S4), render the result with labels within the row
    limit of `memory_tool_limits`. Unknown tool or bad arguments — an error text for
    `function_call_output`, the turn goes on (Т-§11.5)."""
    raise NotImplementedError
