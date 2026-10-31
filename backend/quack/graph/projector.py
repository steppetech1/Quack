"""Projector core: analyze → Query 1 → Phase A → Query 2 → Phase B → Query 3 → Phase C.

The same code runs for incremental apply, rebuild and session overlay (Т-§2.5).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from quack.config import FoldParams
from quack.events.models import StoredEvent
from quack.graph.folds import (
    EvidenceTrace,
    ExposureTrace,
    SkillPrior,
    recompute_misconception_fold,
    recompute_skill_fold,
)
from quack.graph.ids import CURRENT_RULES_VERSION, CURRENT_RULES_VERSION_NUM, rules_version_number
from quack.graph.memory import InMemoryGraph, prop_bool, prop_float, prop_int, prop_str
from quack.graph.mutations import AdvanceWatermark, Mutation, UpsertNode
from quack.graph.phase_a import apply_event_to_trace
from quack.graph.source import GraphSource, GraphStore
from quack.graph.topic_display import (
    INACTIVE_SKILL_STATUSES,
    PrereqSnapshot,
    SkillSnapshot,
    TopicDisplayResult,
    recompute_topic_display,
)
from quack.graph.types import BatchNeed, Props
from quack.graph.view import BatchView


@dataclass
class ProjectionResult:
    mutations: list[Mutation]
    applied_seq: int
    view_graph: InMemoryGraph
    dirty_skill_ids: set[str] = field(default_factory=set)
    dirty_misconception_ids: set[str] = field(default_factory=set)
    dirty_topic_ids: set[str] = field(default_factory=set)


type BatchStatus = Literal["applied", "empty", "skipped_rules"]


@dataclass(frozen=True)
class ProjectorBatchOutcome:
    status: BatchStatus
    applied_seq: int
    mutation_count: int


# --- analyze ---------------------------------------------------------------------------


def _collect_ids(value: Any, key: str, out: set[str]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _collect_ids(v, k, out)
    elif isinstance(value, list):
        for item in value:
            _collect_ids(item, key, out)
    elif isinstance(value, str) and (key.endswith("_id") or key.endswith("_ids")):
        try:
            out.add(str(UUID(value)))
        except ValueError:
            return


def analyze_batch(learner_id: UUID, events: Sequence[StoredEvent]) -> BatchNeed:
    need = BatchNeed(learner_id=str(learner_id))
    for event in events:
        if event.domain_id is not None:
            need.domain_ids.add(str(event.domain_id))
        domain = event.payload.get("domain_id")
        if isinstance(domain, str):
            need.domain_ids.add(domain)
        _collect_ids(event.payload, "", need.node_ids)
        if event.type == "evidence.superseded":
            need.superseded_seqs.add(int(event.payload["target_event_seq"]))
    need.node_ids |= need.domain_ids
    return need


# --- Phase B helpers -------------------------------------------------------------------


def _evidence_trace(evidence_id: str, props: Props) -> EvidenceTrace:
    kind = prop_str(props, "kind", "skill")
    return EvidenceTrace(
        id=evidence_id,
        seq=prop_int(props, "sourceSeq"),
        at_ms=prop_int(props, "at"),
        kind="manifestation" if kind == "manifestation" else "skill",
        tier=prop_int(props, "tier", 4),
        source_type=prop_str(props, "sourceType", "unknown"),
        score=prop_float(props, "score"),
        independence=prop_float(props, "independence", 1.0),
        tested_depth=prop_float(props, "testedDepth", 0.5),
        strength=prop_float(props, "strength", 1.0),
        polarity_weight=prop_float(props, "polarityWeight", 1.0),
        options_count=prop_int(props, "optionsCount"),
        hint_step=prop_int(props, "hintStep"),
        shown_solution=prop_str(props, "assistance", "none") == "shown_solution",
        direction=prop_str(props, "direction", "positive"),
        family_id=prop_str(props, "familyId", "unknown"),
        superseded=prop_bool(props, "superseded"),
    )


def _skill_evidence(graph: InMemoryGraph, skill_id: str) -> list[EvidenceTrace]:
    traces = []
    for key in graph.in_rels("Skill", skill_id, "EVIDENCE_FOR"):
        props = graph.get("Evidence", key[1])
        if key[0] == "Evidence" and props is not None:
            traces.append(_evidence_trace(key[1], props))
    return traces


def _skill_exposures(graph: InMemoryGraph, skill_id: str) -> list[ExposureTrace]:
    traces = []
    for key in graph.in_rels("Skill", skill_id, "EXPOSED_TO"):
        props = graph.get("Exposure", key[1])
        if key[0] == "Exposure" and props is not None:
            traces.append(
                ExposureTrace(
                    id=key[1],
                    seq=prop_int(props, "sourceSeq"),
                    at_ms=prop_int(props, "at"),
                    depth=prop_float(props, "depth"),
                )
            )
    return traces


def _live(graph: InMemoryGraph, label: str, ids: Iterable[str]) -> list[str]:
    result = []
    for node_id in sorted(set(ids)):
        props = graph.get(label, node_id)
        if props is not None and props.get("status") != "merged":
            result.append(node_id)
    return result


def _about_skill(graph: InMemoryGraph, misc_id: str) -> str | None:
    rels = graph.out_rels("Misconception", misc_id, "ABOUT_SKILL")
    return rels[0][4] if rels else None


def _with_alias_sources(view: BatchView, ids: Iterable[str]) -> list[str]:
    wanted = set(ids)
    sources = {a for a in view.aliases if view.resolve(a) in wanted}
    return sorted(wanted | sources)


# --- Phase C helpers -------------------------------------------------------------------


def _skill_snapshot(skill_id: str, props: Props) -> SkillSnapshot:
    return SkillSnapshot(
        skill_id=skill_id,
        status=prop_str(props, "status", "unknown"),
        p_mastery=prop_float(props, "pMastery"),
        confidence=prop_float(props, "confidence"),
        evidence_count=prop_int(props, "evidenceCount"),
        exposure_count=prop_int(props, "exposureCount"),
        mastery_status=prop_str(props, "masteryStatus", "unknown"),
        next_review_at=prop_int(props, "nextReviewAt"),
        last_practiced_at=prop_int(props, "lastPracticedAt"),
    )


def _topic_inputs(
    graph: InMemoryGraph, topic_id: str
) -> tuple[list[SkillSnapshot], dict[str, int], int]:
    skills: list[SkillSnapshot] = []
    dependents: dict[str, int] = {}
    as_of = 0
    for key in graph.in_rels("Topic", topic_id, "PART_OF"):
        props = graph.get("Skill", key[1])
        if key[0] != "Skill" or props is None:
            continue
        skills.append(_skill_snapshot(key[1], props))
        dependents[key[1]] = sum(
            1
            for dep in graph.in_rels("Skill", key[1], "DEPENDS_ON")
            if prop_str(graph.get("Skill", dep[1]), "status") not in INACTIVE_SKILL_STATUSES
        )
        if prop_str(props, "status") not in INACTIVE_SKILL_STATUSES:
            as_of = max(as_of, prop_int(props, "lastPracticedAt"), prop_int(props, "lastExposedAt"))
    return skills, dependents, as_of


async def _phase_c(view: BatchView, source: GraphSource, dirty_topics: set[str]) -> None:
    graph = view.graph
    topics = _live(graph, "Topic", (view.resolve(t) for t in dirty_topics))
    if not topics:
        return
    view.load(await source.load_topic_context(view.learner_id, topics))
    successors = {
        key[4]
        for t in topics
        for key in graph.out_rels("Topic", t, "PREREQ_OF")
        if key[3] == "Topic"
    }
    extra = _live(graph, "Topic", successors - set(topics))
    if extra:
        view.load(await source.load_topic_context(view.learner_id, extra))
    all_topics = sorted(set(topics) | set(extra))
    # Pass 1: aggregates (independent of prerequisites); pass 2: display state.
    aggregates: dict[str, TopicDisplayResult] = {}
    inputs = {t: _topic_inputs(graph, t) for t in all_topics}
    for t in all_topics:
        skills, dependents, as_of = inputs[t]
        aggregates[t] = recompute_topic_display(skills, dependents, [], as_of, view.params)
    for t in all_topics:
        skills, dependents, as_of = inputs[t]
        prereqs = []
        for key in graph.in_rels("Topic", t, "PREREQ_OF"):
            props = graph.get("Topic", key[1])
            if key[0] != "Topic" or props is None:
                continue
            mastery = (
                aggregates[key[1]].mastery if key[1] in aggregates else prop_float(props, "mastery")
            )
            prereqs.append(PrereqSnapshot(key[1], prop_str(props, "status", "unknown"), mastery))
        result = recompute_topic_display(skills, dependents, prereqs, as_of, view.params)
        view.emit(UpsertNode("Topic", t, result.to_props()))


# --- projection ------------------------------------------------------------------------


async def project_events(
    learner_id: UUID,
    events: Sequence[StoredEvent],
    source: GraphSource,
    params: FoldParams,
    *,
    with_watermark: bool = True,
) -> ProjectionResult:
    """Run phases A–C over `events` against `source`; returns mutations (not applied)."""
    ordered = sorted(events, key=lambda e: e.seq)
    view = BatchView(learner_id=str(learner_id), params=params)
    # Query 1.
    view.load(await source.load_batch_slice(analyze_batch(learner_id, ordered)))

    # Phase A.
    dirty_skills: set[str] = set()
    dirty_miscs: set[str] = set()
    dirty_topics: set[str] = set()
    for event in ordered:
        result = apply_event_to_trace(event, view)
        dirty_skills |= result.dirty_skill_ids
        dirty_miscs |= result.dirty_misconception_ids
        dirty_topics |= result.dirty_topic_ids

    # Query 2 + Phase B.
    graph = view.graph
    skills = _live(graph, "Skill", (view.resolve(s) for s in dirty_skills))
    miscs = _live(graph, "Misconception", (view.resolve(m) for m in dirty_miscs))
    about = {s for m in miscs if (s := _about_skill(graph, m)) is not None}
    if skills or miscs:
        view.load(
            await source.load_fold_history(
                view.learner_id,
                _with_alias_sources(view, set(skills) | about),
                _with_alias_sources(view, miscs),
            )
        )
    for skill_id in skills:
        props = graph.get("Skill", skill_id) or {}
        prior = SkillPrior(prop_float(props, "priorMastery"), prop_float(props, "priorConfidence"))
        fold = recompute_skill_fold(
            _skill_evidence(graph, skill_id), _skill_exposures(graph, skill_id), prior, params
        )
        view.emit(UpsertNode("Skill", skill_id, fold.to_props()))
        topic = prop_str(props, "topicId")
        if topic:
            dirty_topics.add(topic)
    for misc_id in miscs:
        props = graph.get("Misconception", misc_id) or {}
        manifestations = [
            _evidence_trace(key[1], graph.get("Evidence", key[1]) or {})
            for key in graph.in_rels("Misconception", misc_id, "OBSERVED_MISCONCEPTION")
            if key[0] == "Evidence"
        ]
        skill_id = _about_skill(graph, misc_id)
        skill_evidence = _skill_evidence(graph, skill_id) if skill_id else []
        fold = recompute_misconception_fold(
            manifestations,
            skill_evidence,
            prop_int(props, "resolvedAt"),
            prop_int(props, "resolvedSeq"),
            params,
        )
        view.emit(UpsertNode("Misconception", misc_id, fold.to_props()))

    # Query 3 + Phase C.
    await _phase_c(view, source, dirty_topics)

    applied_seq = ordered[-1].seq if ordered else 0
    if with_watermark and ordered:
        view.emit(AdvanceWatermark(view.learner_id, applied_seq, CURRENT_RULES_VERSION))
    return ProjectionResult(
        mutations=list(view.mutations),
        applied_seq=applied_seq,
        view_graph=view.graph,
        dirty_skill_ids=set(skills),
        dirty_misconception_ids=set(miscs),
        dirty_topic_ids=dirty_topics,
    )


def rules_mismatch(projector_version: str | None) -> bool:
    """True if a stored projectorVersion differs from CURRENT_RULES_VERSION (spec §4.4)."""
    if not projector_version:
        return False
    try:
        return rules_version_number(projector_version) != CURRENT_RULES_VERSION_NUM
    except ValueError:
        return True


async def run_projector_batch(
    learner_id: UUID, events: Sequence[StoredEvent], store: GraphStore, params: FoldParams
) -> ProjectorBatchOutcome:
    """Rules guard + projection + one-transaction apply (with AdvanceWatermark)."""
    watermark = await store.load_watermark(str(learner_id))
    applied = watermark.applied_seq if watermark else 0
    if watermark is not None and rules_mismatch(watermark.projector_version):
        return ProjectorBatchOutcome("skipped_rules", applied, 0)
    pending = sorted((e for e in events if e.seq > applied), key=lambda e: e.seq)
    if not pending:
        return ProjectorBatchOutcome("empty", applied, 0)
    expected = list(range(applied + 1, applied + 1 + len(pending)))
    if [e.seq for e in pending] != expected:
        raise ValueError(f"events must continue the watermark {applied} without gaps")
    result = await project_events(learner_id, pending, store, params)
    await store.apply(result.mutations)
    return ProjectorBatchOutcome("applied", result.applied_seq, len(result.mutations))


async def project_in_memory(
    learner_id: UUID, events: Sequence[StoredEvent], params: FoldParams, *, batch_size: int
) -> InMemoryGraph:
    """Rebuild from scratch in memory: an empty graph, events applied in batches."""
    graph = InMemoryGraph()
    ordered = sorted(events, key=lambda e: e.seq)
    for start in range(0, len(ordered), max(1, batch_size)):
        await run_projector_batch(learner_id, ordered[start : start + batch_size], graph, params)
    return graph
