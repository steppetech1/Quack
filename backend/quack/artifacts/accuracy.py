"""Accuracy of what the artifact writer returns (ТЗ Ф4 WP23.3, WP23.2; Т-§17.3, Т-§17.4;
D-C-155–D-C-158). One code for the notes of a topic and the cheat sheet of a hub.

What code checks on the output of `artifact_writer@1` (the skill of the learner cannot cancel it):

- labels and block numbers are real (the writer names chunks and skills by short labels);
- a `definition` or `statement` names the chunk it rests on — the page or slide comes from the
  chunk (`sources[].page`), the writer cannot invent one — whenever the context has chunks at
  all (T-AR2);
- every formula the block declares in `formulas` (an identity `left = right`) passes compute
  `/v1/math/check` in the mode `equal`.

A violation is one more generation; after the second one the element is dropped — except a
block with a formula that did not pass: it stays with `unverified = True` («не проверено»,
Т-§17.4). A block that cites no chunk is marked `basis = "system"` («без опоры»: an explanation
of the system, not a quote of the course); a block that cites one — `basis = "materials"`. When
compute does not answer the writer is not at fault: no new generation, the block is marked
`unverified` (law 8: «не проверено» is an explicit value).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol
from uuid import UUID

from quack.artifacts.documents import Block, SystemBlock
from quack.artifacts.writer import ArtifactWriterOutput, WrittenBlock
from quack.compute_client.client import ComputeClient, ComputeUnavailableError
from quack.compute_client.math_contracts import (
    MathCheckRequest,
    MathInput,
    NumericParams,
    Tolerance,
)
from quack.config import Settings
from quack.llm.gateway import LLMGateway
from quack.llm.labels import LabelMap, UnknownLabelError
from quack.logging import get_logger

if TYPE_CHECKING:
    from quack.graph.read.materials import ChunkHit

log = get_logger(__name__)

WRITER_ROLE = "artifact_writer"
# Kinds that, taken from the materials, must name their chunk (Т-§17.3).
SOURCED_KINDS = frozenset({"definition", "statement"})
_NOT_AN_EQUALITY = "<>!:"  # `<=`, `>=`, `!=`, `:=`

type FormulaVerdict = Literal["passed", "failed", "unavailable"]


class BlockContext(Protocol):
    """What the checks need of the writer's input (`WriterInput` of the topic, the hub's)."""

    @property
    def variables(self) -> dict[str, str]: ...
    @property
    def labels(self) -> LabelMap: ...
    @property
    def chunks(self) -> dict[UUID, ChunkHit]: ...
    @property
    def current(self) -> tuple[Block, ...]: ...


@dataclass(frozen=True)
class Checked:
    """The blocks that stay (ids instead of labels, marks set) and the number of violations."""

    blocks: list[SystemBlock]
    violations: int


# --- formulas -------------------------------------------------------------------------------


def split_identity(formula: str, max_chars: int) -> tuple[str, str] | None:
    """`left = right` with exactly one `=` outside brackets and braces; `None` for anything
    else (an inequality, a chain `a = b = c`, no sign) — compute checks identities only."""
    depth, cut = 0, None
    for index, char in enumerate(formula):
        if char in "{([":
            depth += 1
        elif char in "})]":
            depth -= 1
        elif char == "=" and depth == 0:
            before = formula[index - 1] if index else ""
            after = formula[index + 1 : index + 2]
            if before in _NOT_AN_EQUALITY or after == "=" or cut is not None:
                return None
            cut = index
    if cut is None:
        return None
    left, right = formula[:cut].strip(), formula[cut + 1 :].strip()
    if not left or not right or max(len(left), len(right)) > max_chars:
        return None
    return left, right


def formula_request(formula: str, settings: Settings) -> MathCheckRequest | None:
    """The `/v1/math/check` request of an identity: both sides as LaTeX, mode `equal`, the
    budget of a background check; every threshold from `settings` (§0.3)."""
    sides = split_identity(formula, settings.artifact_formula_chars_max)
    if sides is None:
        return None
    left, right = sides
    return MathCheckRequest(
        kind="expression",
        student=MathInput(latex=left),
        reference=MathInput(latex=right),
        mode="equal",
        tolerance=Tolerance(min_sig_digits=settings.min_sig_digits),
        numeric=NumericParams(
            numeric_points=settings.numeric_points,
            simplify_share=settings.math_simplify_share,
            precision_digits=settings.math_precision_digits,
            sample_low=settings.math_sample_low,
            sample_high=settings.math_sample_high,
            positive_sample_min=settings.math_positive_sample_min,
            max_attempts_per_point=settings.math_max_attempts_per_point,
            rel_tol=settings.math_rel_tol,
            abs_tol=settings.math_abs_tol,
        ),
        timeout_ms=settings.symbolic_background_timeout_ms,
    )


async def verify_formula(
    formula: str, compute: ComputeClient | None, settings: Settings
) -> FormulaVerdict:
    """`passed` — compute proved the identity; `failed` — it did not (wrong, `unclear`, not
    parsed, not an identity); `unavailable` — no compute or it did not answer in time."""
    request = formula_request(formula, settings)
    if request is None:
        return "failed"
    if compute is None:
        return "unavailable"
    try:
        response = await asyncio.wait_for(compute.math_check(request), request.timeout_ms / 1000)
    except (TimeoutError, ComputeUnavailableError):
        log.info("artifact_formula_unavailable")
        return "unavailable"
    passed = response.status == "completed" and response.outcome == "correct"
    return "passed" if passed else "failed"


# --- blocks ---------------------------------------------------------------------------------


def system_block(item: WrittenBlock, context: BlockContext) -> SystemBlock:
    """The writer's block with ids instead of labels and the mark of what it rests on;
    `UnknownLabelError` / `IndexError` — a label or a block number that does not exist."""
    target = None
    if item.updates is not None:
        if item.updates > len(context.current):
            raise IndexError(item.updates)
        target = context.current[item.updates - 1].block_id
    sources = []
    for label in item.sources:
        hit = context.chunks[context.labels.resolve("chunk", label)]
        sources.append(
            {
                "chunk_id": str(hit.chunk_id),
                "material_id": str(hit.material_id) if hit.material_id else None,
                "page": hit.page,
            }
        )
    skills = [str(context.labels.resolve("skill", label)) for label in item.skills]
    return SystemBlock(
        block_id=target,
        kind=item.kind,
        content=item.content,
        sources=sources,
        skills=skills,
        basis="materials" if sources else "system",
    )


def lacks_source(item: WrittenBlock, context: BlockContext) -> bool:
    """T-AR2: a definition or statement without a chunk while the materials have chunks. With
    no chunk in the context there is nothing to cite — the block stays, as «без опоры»."""
    return item.kind in SOURCED_KINDS and not item.sources and bool(context.chunks)


async def check_written(
    written: Sequence[WrittenBlock],
    context: BlockContext,
    compute: ComputeClient | None,
    settings: Settings,
) -> Checked:
    blocks: list[SystemBlock] = []
    violations = 0
    for item in written:
        try:
            block = system_block(item, context)
        except (UnknownLabelError, IndexError, KeyError):
            violations += 1
            continue
        if lacks_source(item, context):
            violations += 1
            continue
        cap = settings.artifact_formulas_per_block_max
        verdicts = [await verify_formula(f, compute, settings) for f in item.formulas[:cap]]
        if "failed" in verdicts:
            violations += 1  # a regeneration may fix it; if not, the block stays marked
        unverified = len(item.formulas) > cap or any(v != "passed" for v in verdicts)
        blocks.append(block.model_copy(update={"unverified": unverified}) if unverified else block)
    return Checked(blocks, violations)


async def write_blocks(
    gateway: LLMGateway,
    settings: Settings,
    compute: ComputeClient | None,
    context: BlockContext,
    *,
    learner_id: UUID,
    key: str,
) -> tuple[list[SystemBlock], str]:
    """The checked blocks of `artifact_writer@N` and the prompt (`artifact_writer@N`, §0.4) that
    wrote them. Т-§17.3: a violation — one more generation (`key:retry`), then the element is
    dropped; a block whose formula did not pass twice stays marked «не проверено»."""
    prompt = settings.llm_roles[WRITER_ROLE].prompt
    if prompt is None:
        raise ValueError("role 'artifact_writer' has no prompt")
    blocks: list[SystemBlock] = []
    for attempt in range(2):
        result = await gateway.generate_structured(
            WRITER_ROLE,
            int(prompt.rsplit("@", 1)[1]),
            ArtifactWriterOutput,
            context.variables,
            learner_id,
            None,
            None,
            key + (":retry" if attempt else ""),
        )
        checked = await check_written(result.value.blocks, context, compute, settings)
        blocks = checked.blocks
        if checked.violations == 0:
            break
        log.info("artifact_writer_violations", key=key, bad=checked.violations, attempt=attempt)
    return blocks, prompt
