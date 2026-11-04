"""Memory tools of the teacher (Т-§10.4; ТЗ Ф4 WP14.4, seam S11 / S12).

Six functions passed to the model with `tool_choice: auto` (Т-§11.5). Arguments and
results use short labels (Т-§4.5): skills `S…`, tasks `K…`, evidence `E…`, canvas lines
`L…` (D-B-108). Results are text and count against the context budget; their row limits
are `Settings.memory_tool_limits` (D-B-103). With Modal down, `search_materials` and
`similar_mistakes(text)` return `SEARCH_UNAVAILABLE` (Т-§11.9, ТЗ Ф4 §4.4).
Agent requests are not a tool: they are `agentRequests` of the turn output (Т-§10.4).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quack.config import Settings
from quack.embeddings.client import EmbeddingsUnavailable
from quack.graph.read import GraphReader
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.llm.strict_schema import to_strict_json_schema
from quack.session.memory_picture import LEVEL_NAMES

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


@dataclass(frozen=True)
class MaterialChunk:
    """A material chunk (Т-§10.5): text with the reference to its page or slide; slot 8 and
    `search_materials` show it the same way. `material_name` — None: the documents give a
    material no name (`Т-§3.4`, `content.materials`), the source is the label and the page
    (D-B-171)."""

    chunk_id: UUID
    material_name: str | None
    page: int | None
    text: str


def render_chunks(chunks: Sequence[MaterialChunk], label: Callable[[UUID], str | None]) -> str:
    """Each chunk as a quote from the material with its source (Т-§19.8): material text is
    data, not instructions."""
    out: list[str] = []
    for chunk in chunks:
        name = label(chunk.chunk_id)
        page = f", page {chunk.page}" if chunk.page is not None else ""
        source = (
            f'From the material "{chunk.material_name}"'
            if chunk.material_name
            else "From a material"
        )
        out.append((f"{name}. " if name else "") + f"{source}{page}:")
        out.extend(f"> {line}" for line in chunk.text.splitlines())
    return "\n".join(out)


@dataclass(frozen=True)
class CanvasLine:
    """A canvas line of the session with its device time (`canvas.line_committed`)."""

    text: str
    started_at_ms: int
    ended_at_ms: int


@dataclass(frozen=True)
class VoiceSegment:
    """A transcribed voice segment (`voice.transcribed`, Т-§22.8)."""

    text: str
    started_at_ms: int
    ended_at_ms: int


type SearchMaterials = Callable[[str, Sequence[UUID], int], Awaitable[list[MaterialChunk]]]


@dataclass
class ToolContext:
    """What a tool call reads with (WP14.4): the learner, the label table of the call, the
    memory read (`quack.graph.read`, seam S4), the names of skills, the canvas lines of the
    session by their numbers `L1…` (the numbers of slot 5, D-B-163) and its voice segments, the
    domain of the session (a mistake elsewhere is "another subject"), the
    material search (seam S12; `None` while Modal is down; already bound to `learner_id` of this
    turn by the assembly, isolation §4.3 rests on that binding). Evidence and chunks the tools
    show get labels `E…`, `P…` for this turn: a later tool call may name them."""

    learner_id: UUID
    labels: LabelMap
    reader: GraphReader
    settings: Settings
    now_ms: int
    domain_id: UUID | None = None
    skill_names: Mapping[UUID, str] = field(default_factory=dict)
    canvas_lines: Mapping[str, CanvasLine] = field(default_factory=dict)
    voice: Sequence[VoiceSegment] = ()
    search: SearchMaterials | None = None
    evidence: dict[str, UUID] = field(default_factory=dict)
    chunks: dict[str, UUID] = field(default_factory=dict)

    def evidence_label(self, evidence_id: UUID) -> str:
        for label, known in self.evidence.items():
            if known == evidence_id:
                return label
        label = f"E{len(self.evidence) + 1}"
        self.evidence[label] = evidence_id
        return label

    def chunk_label(self, chunk_id: UUID) -> str:
        for label, known in self.chunks.items():
            if known == chunk_id:
                return label
        label = f"P{len(self.chunks) + 1}"
        self.chunks[label] = chunk_id
        return label


class ToolArgumentError(ValueError):
    """Bad arguments of a tool call: the text goes back to the model, the turn goes on."""


_DAY_MS = 86_400_000
_OUTCOME = {
    "correct": "right",
    "incorrect": "wrong",
    "partial": "partly right",
    "unclear": "unclear",
}
_SOURCE = {
    "code": "checked by code",
    "model": "judged by a model",
    "conversation": "from the conversation",
}
_STATUS = {
    "proven": "proven",
    "implied": "follows from a proven level",
    "unstable": "not always",
    "failed": "failed",
    "not_tested": "not checked",
}


def _when(at_ms: int, now_ms: int) -> str:
    days = max(0, (now_ms - at_ms) // _DAY_MS)
    return "today" if days == 0 else "yesterday" if days == 1 else f"{days} days ago"


def _skill(ctx: ToolContext, label: str) -> UUID:
    try:
        return ctx.labels.resolve("skill", label)
    except UnknownLabelError as exc:
        raise ToolArgumentError(f"unknown skill label {label!r}") from exc


def _skill_name(ctx: ToolContext, skill_id: UUID) -> str:
    name = ctx.skill_names.get(skill_id, "an unnamed skill")
    label = ctx.labels.label("skill", skill_id)
    return f"{label} {name}" if label else name


def _task(ctx: ToolContext, task_id: UUID | None) -> str | None:
    if task_id is None:
        return None
    return ctx.labels.label("task", task_id) or "an earlier task"


def _limit(ctx: ToolContext, name: ToolName) -> int:
    return ctx.settings.memory_tool_limits[name]


async def _skill_history(ctx: ToolContext, args: SkillHistoryArgs) -> str:
    skill_id = _skill(ctx, args.skill)
    facts = await ctx.reader.skill_history(skill_id, args.level, _limit(ctx, "skill_history"))
    lines = []
    for fact in facts[: _limit(ctx, "skill_history")]:
        parts = [
            f"level {fact.level} {LEVEL_NAMES[fact.level]}",
            _OUTCOME[fact.outcome],
            _SOURCE[fact.source],
        ]
        if fact.assistance == "hint":
            parts.append(
                f"after hint step {fact.hint_step} of 3" if fact.hint_step else "after a hint"
            )
        elif fact.assistance == "shown_solution":
            parts.append("after the solution was shown")
        parts.append(_when(fact.occurred_at_ms, ctx.now_ms))
        task = _task(ctx, fact.task_id)
        if task:
            parts.insert(0, f"task {task}")
        line = f"{ctx.evidence_label(fact.evidence_id)}: " + ", ".join(parts)
        lines.append(line + (f': "{fact.quote}"' if fact.quote else ""))
    return "\n".join(lines) or "no evidence yet"


async def _similar_mistakes(ctx: ToolContext, args: SimilarMistakesArgs) -> str:
    if (args.evidence is None) == (args.text is None):
        raise ToolArgumentError("give exactly one of evidence and text")
    evidence_id = None
    if args.evidence is not None:
        evidence_id = ctx.evidence.get(args.evidence)
        if evidence_id is None:
            raise ToolArgumentError(f"unknown evidence label {args.evidence!r}")
    elif ctx.search is None:
        return SEARCH_UNAVAILABLE  # a text needs the embedding of Modal (ТЗ Ф4 §4.4)
    found = await ctx.reader.similar_mistakes(ctx.learner_id, evidence_id, args.text)
    lines = []
    for item in found[: _limit(ctx, "similar_mistakes")]:
        where = _skill_name(ctx, item.skill_id)
        if item.domain_id is not None and item.domain_id != ctx.domain_id:
            where += " (another subject)"
        line = f"{ctx.evidence_label(item.evidence_id)}: {where}"
        lines.append(line + (f': "{item.quote}"' if item.quote else ""))
    return "\n".join(lines) or "no similar mistakes"


async def _past_solution(ctx: ToolContext, args: PastSolutionArgs) -> str:
    if (args.task is None) == (args.skill is None):
        raise ToolArgumentError("give exactly one of task and skill")
    task_id = skill_id = None
    if args.task is not None:
        try:
            task_id = ctx.labels.resolve("task", args.task)
        except UnknownLabelError as exc:
            raise ToolArgumentError(f"unknown task label {args.task!r}") from exc
    else:
        skill_id = _skill(ctx, args.skill or "")
    found = await ctx.reader.past_solution(ctx.learner_id, task_id, skill_id)
    blocks = []
    for item in found[: _limit(ctx, "past_solution")]:
        task = _task(ctx, item.task_id)
        head = f"Solution of {_skill_name(ctx, item.skill_id)}"
        head += f", task {task}" if task else ""
        head += f", {_when(item.occurred_at_ms, ctx.now_ms)}:"
        blocks.append("\n".join([head, *(f"  {line}" for line in item.lines)]))
    return "\n".join(blocks) or "no past solutions"


async def _voice_at_line(ctx: ToolContext, args: VoiceAtLineArgs) -> str:
    line = ctx.canvas_lines.get(args.line)
    if line is None:
        raise ToolArgumentError(f"unknown canvas line {args.line!r}")
    said = [
        segment.text
        for segment in ctx.voice
        if segment.started_at_ms <= line.ended_at_ms and segment.ended_at_ms >= line.started_at_ms
    ]
    return "\n".join(said[: _limit(ctx, "voice_at_line")]) or "the learner said nothing then"


async def _search_materials(ctx: ToolContext, args: SearchMaterialsArgs) -> str:
    if ctx.search is None:
        return SEARCH_UNAVAILABLE
    skill_ids = [_skill(ctx, label) for label in args.skills]
    limit = _limit(ctx, "search_materials")
    try:
        chunks = (await ctx.search(args.query, skill_ids, limit))[:limit]
    except EmbeddingsUnavailable:
        return SEARCH_UNAVAILABLE  # Modal is down: the tool says so, the turn goes on (ТЗ Ф4 §4.4)
    return render_chunks(chunks, ctx.chunk_label) or "nothing found"


async def _prerequisites(ctx: ToolContext, args: PrerequisitesArgs) -> str:
    skill_id = _skill(ctx, args.skill)
    found = await ctx.reader.prerequisites(skill_id, _limit(ctx, "prerequisites"))
    lines = []
    for item in found[: _limit(ctx, "prerequisites")]:
        levels = "; ".join(
            f"{LEVEL_NAMES[level.level]} — {_STATUS[level.status]}" for level in item.levels
        )
        sure = (
            "" if item.confidence >= ctx.settings.requires_min_confidence else " (uncertain link)"
        )
        lines.append(f"{_skill_name(ctx, item.skill_id)}{sure}: {levels or 'not checked'}")
    return "\n".join(lines) or "no prerequisites known"


async def run_tool(name: str, arguments: str, ctx: ToolContext) -> str:
    """Execute one tool call: parse `arguments` (JSON) by the tool's model, read memory
    through `quack.graph.read` (seam S4), render the result with labels within the row
    limit of `memory_tool_limits`. Unknown tool or bad arguments — an error text for
    `function_call_output`, the turn goes on (Т-§11.5)."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        return f"error: unknown tool {name!r}"
    try:
        args = tool.args.model_validate_json(arguments)
    except ValidationError as exc:
        return f"error: bad arguments of {name}: {exc.errors()[0]['msg']}"
    try:
        match args:
            case SkillHistoryArgs():
                return await _skill_history(ctx, args)
            case SimilarMistakesArgs():
                return await _similar_mistakes(ctx, args)
            case PastSolutionArgs():
                return await _past_solution(ctx, args)
            case VoiceAtLineArgs():
                return await _voice_at_line(ctx, args)
            case SearchMaterialsArgs():
                return await _search_materials(ctx, args)
            case PrerequisitesArgs():
                return await _prerequisites(ctx, args)
            case _:
                return f"error: unknown tool {name!r}"
    except ToolArgumentError as exc:
        return f"error: {exc}"
