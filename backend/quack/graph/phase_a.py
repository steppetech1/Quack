"""Phase A: event → trace and structure mutations (spec §4.3 step 2, Т-§7.8, Т-§4.4).

Handlers are pure: they read only the batch view and the event, use only event time, and
create ids with uuid5 (spec §2.3). Every id from a payload is resolved through Alias first.
A reference to a missing node makes that part of the event a no-op (Т-§2.4).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from quack.events import payloads as p
from quack.events.models import StoredEvent
from quack.graph.folds import MisconceptionFoldResult, SkillFoldResult
from quack.graph.ids import deterministic_id
from quack.graph.memory import prop_int, prop_str
from quack.graph.mutations import DeleteRel, Mutation, RebindNodeRel, UpsertNode, UpsertRel
from quack.graph.topic_display import TopicDisplayResult
from quack.graph.types import Props
from quack.graph.view import BatchView

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Initial computed properties of new nodes: the folds of an empty history.
EMPTY_SKILL_FOLD: Props = SkillFoldResult(
    p_mastery=0.0,
    confidence=0.0,
    evidence_count=0,
    distinct_families=0,
    decay_rate=0.0,
    stability=0.0,
    last_practiced_at=0,
    last_reviewed_at=0,
    next_review_at=0,
    mastery_status="unknown",
    exposure_count=0,
    last_exposed_at=0,
).to_props()
EMPTY_MISCONCEPTION_FOLD: Props = MisconceptionFoldResult(
    status="latent",
    occurrences=0,
    first_seen_at=0,
    last_seen_at=0,
    watch_since_at=0,
    fixed_at=0,
    resist_count=0,
).to_props()
EMPTY_TOPIC_DISPLAY: Props = TopicDisplayResult(
    mastery=0.0,
    coverage=0.0,
    confidence=0.0,
    hub_score=0.0,
    nearest_review_at=0,
    display_state="available",
    skill_count=0,
).to_props()

# Evidence tier by evaluator (Т-§7.2, §7.8): code check → T1, model involved → T2.
EVALUATOR_TIER = {"compute": 1, "llm": 2, "hybrid": 2}
DIAGNOSTIC_TIER = 2
EXTRACTOR_TIER = 3
DIRECTION_SCORE = {"positive": 1.0, "negative": 0.0, "neutral": 0.5}


@dataclass
class PhaseAResult:
    mutations: list[Mutation] = field(default_factory=list)
    dirty_skill_ids: set[str] = field(default_factory=set)
    dirty_misconception_ids: set[str] = field(default_factory=set)
    dirty_topic_ids: set[str] = field(default_factory=set)


def normalize_name(name: str) -> str:
    return name.strip().lower()


def to_ms(moment: datetime) -> int:
    return (moment - _EPOCH) // timedelta(milliseconds=1)


@dataclass
class _Ctx:
    event: StoredEvent
    view: BatchView
    result: PhaseAResult
    skill_ref_index: int = 0

    @property
    def learner(self) -> str:
        return self.view.learner_id

    @property
    def seq(self) -> int:
        return self.event.seq

    @property
    def at(self) -> int:
        return to_ms(self.event.occurred_at)

    @property
    def iso(self) -> str:
        return self.event.occurred_at.astimezone(UTC).isoformat()

    def item_id(self, item_index: int | str) -> str:
        return deterministic_id(self.learner, self.seq, item_index)

    def emit(self, mutation: Mutation) -> None:
        self.view.emit(mutation)
        self.result.mutations.append(mutation)

    def node(self, label: str, node_id: UUID | str | None) -> tuple[str, Props] | None:
        if node_id is None:
            return None
        resolved = self.view.resolve(str(node_id))
        props = self.view.graph.get(label, resolved)
        return (resolved, props) if props is not None else None

    def set(self, label: str, node_id: str, props: Props) -> None:
        self.emit(UpsertNode(label, node_id, props))

    def rel(self, fl: str, fid: str, rt: str, tl: str, tid: str, props: Props | None = None):
        self.emit(UpsertRel(fl, fid, rt, tl, tid, props or {}))

    # --- dirty marking ---

    def dirty_skill(self, skill_id: str) -> None:
        self.result.dirty_skill_ids.add(skill_id)
        props = self.view.graph.get("Skill", skill_id)
        topic = prop_str(props, "topicId")
        if topic:
            self.result.dirty_topic_ids.add(topic)
        for key in self.view.graph.in_rels("Skill", skill_id, "ABOUT_SKILL"):
            self.result.dirty_misconception_ids.add(key[1])

    def dirty_topic(self, topic_id: str) -> None:
        self.result.dirty_topic_ids.add(topic_id)


# --- node factories ---------------------------------------------------------------------


def _create_topic(ctx: _Ctx, topic_id: str, domain_id: str, name: str, desc: str, status: str):
    ctx.set(
        "Topic",
        topic_id,
        {
            "learnerId": ctx.learner,
            "domainId": domain_id,
            "name": name,
            "normName": normalize_name(name),
            "description": desc,
            "status": status,
            "origin": "structure",
            **EMPTY_TOPIC_DISPLAY,
        },
    )
    if ctx.view.graph.get("Domain", domain_id) is not None:
        ctx.rel("Domain", domain_id, "HAS_TOPIC", "Topic", topic_id)
    ctx.dirty_topic(topic_id)


def _create_skill(
    ctx: _Ctx,
    skill_id: str,
    domain_id: str,
    topic_id: str,
    name: str,
    desc: str,
    status: str,
    origin: str,
) -> None:
    ctx.set(
        "Skill",
        skill_id,
        {
            "learnerId": ctx.learner,
            "domainId": domain_id,
            "topicId": topic_id,
            "name": name,
            "normName": normalize_name(name),
            "description": desc,
            "status": status,
            "origin": origin,
            "priorMastery": 0.0,
            "priorConfidence": 0.0,
            "priorSource": "none",
            **EMPTY_SKILL_FOLD,
        },
    )
    ctx.rel("Skill", skill_id, "PART_OF", "Topic", topic_id)
    ctx.dirty_skill(skill_id)


def _create_misconception(
    ctx: _Ctx, misc_id: str, domain_id: str, skill_id: str, name: str, desc: str, origin: str
) -> None:
    ctx.set(
        "Misconception",
        misc_id,
        {
            "learnerId": ctx.learner,
            "domainId": domain_id,
            "skillId": skill_id,
            "name": name,
            "normName": normalize_name(name),
            "description": desc,
            "origin": origin,
            "resolvedAt": 0,
            "resolvedSeq": 0,
            **EMPTY_MISCONCEPTION_FOLD,
        },
    )
    ctx.rel("Misconception", misc_id, "ABOUT_SKILL", "Skill", skill_id)
    ctx.result.dirty_misconception_ids.add(misc_id)


def _set_skill_topic(ctx: _Ctx, skill_id: str, old_topic: str, new_topic: str) -> None:
    if old_topic == new_topic:
        return
    if old_topic:
        ctx.emit(DeleteRel("Skill", skill_id, "PART_OF", "Topic", old_topic))
        ctx.dirty_topic(old_topic)
    ctx.set("Skill", skill_id, {"topicId": new_topic})
    ctx.rel("Skill", skill_id, "PART_OF", "Topic", new_topic)
    ctx.dirty_topic(new_topic)


def _find_by_norm_name(ctx: _Ctx, label: str, domain_id: str, name: str) -> str | None:
    norm = normalize_name(name)
    for node_id, props in ctx.view.graph.nodes_with_label(label):
        if (
            props.get("domainId") == domain_id
            and props.get("normName") == norm
            and props.get("status") != "merged"
        ):
            return node_id
    return None


def _resolve_skill_ref(ctx: _Ctx, ref: p.SkillRef) -> str | None:
    """Existing skill id, or an inline provisional skill reused by normalized name in the
    domain or created with uuid5(..., "skill:{idx}") (Т-§4.4). `idx` counts the SkillRefs of
    the event in payload order."""
    idx = ctx.skill_ref_index
    ctx.skill_ref_index += 1
    if ref.skill_id is not None:
        found = ctx.node("Skill", ref.skill_id)
        return found[0] if found else None
    assert ref.new_skill is not None
    topic = ctx.node("Topic", ref.new_skill.topic_id)
    if topic is None:
        return None
    topic_id, topic_props = topic
    domain_id = prop_str(topic_props, "domainId")
    existing = _find_by_norm_name(ctx, "Skill", domain_id, ref.new_skill.name)
    if existing is not None:
        return existing
    skill_id = ctx.item_id(f"skill:{idx}")
    _create_skill(
        ctx,
        skill_id,
        domain_id,
        topic_id,
        ref.new_skill.name.strip(),
        "",
        "provisional",
        "extracted",
    )
    return skill_id


def _domain_status(ctx: _Ctx, domain_id: str) -> str:
    return prop_str(ctx.view.graph.get("Domain", domain_id), "status", "unknown")


def _task_family(ctx: _Ctx, task_id: UUID) -> tuple[str, str]:
    task = ctx.node("Task", task_id)
    if task is None:
        return "", "unknown"
    return task[0], prop_str(task[1], "familyId", "unknown")


def _evidence(
    ctx: _Ctx,
    item_index: int | str,
    *,
    kind: str,
    target_label: str,
    target_id: str,
    rel_type: str,
    domain_id: str,
    tier: int,
    source_type: str,
    score: float,
    independence: float = 1.0,
    tested_depth: float = 0.5,
    strength: float = 1.0,
    polarity_weight: float = 1.0,
    direction: str = "positive",
    family_id: str = "unknown",
    task_id: str = "",
    at_ms: int | None = None,
    extra: Props | None = None,
) -> str:
    evidence_id = ctx.item_id(item_index)
    ctx.set(
        "Evidence",
        evidence_id,
        {
            "learnerId": ctx.learner,
            "domainId": domain_id,
            "kind": kind,
            "tier": tier,
            "sourceType": source_type,
            "sourceSeq": ctx.seq,
            "eventType": ctx.event.type,
            "at": ctx.at if at_ms is None else at_ms,
            "score": score,
            "independence": independence,
            "testedDepth": tested_depth,
            "strength": strength,
            "polarityWeight": polarity_weight,
            "direction": direction,
            "familyId": family_id,
            "taskId": task_id,
            "sessionId": str(ctx.event.session_id) if ctx.event.session_id else "",
            "turnId": str(ctx.event.turn_id) if ctx.event.turn_id else "",
            "superseded": False,
            "supersededBySeq": 0,
            **(extra or {}),
        },
    )
    ctx.rel("Evidence", evidence_id, rel_type, target_label, target_id)
    return evidence_id


def _skill_domain(ctx: _Ctx, skill_id: str) -> str:
    return prop_str(ctx.view.graph.get("Skill", skill_id), "domainId")


def _misconception_for(
    ctx: _Ctx, name: str, skill_id: str, misc_id: UUID | None, new_index: str, origin: str
) -> str:
    if misc_id is not None:
        found = ctx.node("Misconception", misc_id)
        if found is not None:
            return found[0]
    domain_id = _skill_domain(ctx, skill_id)
    existing = _find_by_norm_name(ctx, "Misconception", domain_id, name)
    if existing is not None:
        return existing
    new_id = ctx.item_id(new_index)
    _create_misconception(ctx, new_id, domain_id, skill_id, name.strip(), "", origin)
    return new_id


def _merge(ctx: _Ctx, label: str, source: UUID, target: UUID) -> tuple[str, str] | None:
    src, tgt = ctx.node(label, source), ctx.node(label, target)
    if src is None or tgt is None or src[0] == tgt[0]:
        return None
    return src[0], tgt[0]


# --- handlers ---------------------------------------------------------------------------


def _learner_registered(ctx: _Ctx, _: p.LearnerRegistered) -> None:
    ctx.set("Learner", ctx.learner, {"learnerId": ctx.learner})


def _no_graph_effect(ctx: _Ctx, _: p.Payload) -> None:
    """Event without a projector handler: only the watermark moves (Т-§2.4)."""


def _domain_created(ctx: _Ctx, e: p.DomainCreated) -> None:
    domain_id = str(e.domain_id)
    ctx.set("Learner", ctx.learner, {"learnerId": ctx.learner})
    ctx.set(
        "Domain",
        domain_id,
        {
            "learnerId": ctx.learner,
            "name": e.name,
            "slug": e.slug,
            "status": "onboarding",
            "hasMaterials": e.has_materials,
            "structureVersion": 0,
        },
    )
    ctx.rel("Learner", ctx.learner, "OWNS", "Domain", domain_id)


def _set_domain(ctx: _Ctx, domain_id: UUID, props: Props) -> None:
    found = ctx.node("Domain", domain_id)
    if found is not None:
        ctx.set("Domain", found[0], props)


def _domain_archived(ctx: _Ctx, e: p.DomainArchived) -> None:
    _set_domain(ctx, e.domain_id, {"status": "archived"})


def _interview_completed(ctx: _Ctx, e: p.StructureInterviewCompleted) -> None:
    _set_domain(ctx, e.domain_id, {"status": "structuring"})


def _upsert_topic_draft(ctx: _Ctx, domain_id: str, draft: p.TopicDraft, status: str) -> None:
    found = ctx.node("Topic", draft.topic_id)
    if found is None:
        _create_topic(
            ctx, str(draft.topic_id), domain_id, draft.name, draft.description or "", status
        )
        return
    ctx.set(
        "Topic",
        found[0],
        {
            "name": draft.name,
            "normName": normalize_name(draft.name),
            "description": draft.description or "",
        },
    )
    ctx.dirty_topic(found[0])


def _upsert_skill_draft(ctx: _Ctx, domain_id: str, draft: p.SkillDraft, status: str) -> None:
    topic = ctx.node("Topic", draft.topic_id)
    if topic is None:
        return
    found = ctx.node("Skill", draft.skill_id)
    if found is None:
        _create_skill(
            ctx,
            str(draft.skill_id),
            domain_id,
            topic[0],
            draft.name,
            draft.description or "",
            status,
            "structure",
        )
        return
    skill_id, props = found
    ctx.set(
        "Skill",
        skill_id,
        {
            "name": draft.name,
            "normName": normalize_name(draft.name),
            "description": draft.description or "",
        },
    )
    _set_skill_topic(ctx, skill_id, prop_str(props, "topicId"), topic[0])
    ctx.dirty_skill(skill_id)


def _add_prereq(ctx: _Ctx, edge: p.PrereqEdge) -> None:
    a, b = ctx.node("Topic", edge.from_topic_id), ctx.node("Topic", edge.to_topic_id)
    if a is None or b is None or a[0] == b[0]:
        return
    ctx.rel("Topic", a[0], "PREREQ_OF", "Topic", b[0])
    ctx.dirty_topic(b[0])


def _remove_prereq(ctx: _Ctx, edge: p.PrereqEdge) -> None:
    a, b = ctx.node("Topic", edge.from_topic_id), ctx.node("Topic", edge.to_topic_id)
    if a is None or b is None:
        return
    ctx.emit(DeleteRel("Topic", a[0], "PREREQ_OF", "Topic", b[0]))
    ctx.dirty_topic(b[0])


def _add_skill_dep(ctx: _Ctx, edge: p.SkillDepEdge) -> None:
    a, b = ctx.node("Skill", edge.skill_id), ctx.node("Skill", edge.depends_on_skill_id)
    if a is None or b is None or a[0] == b[0]:
        return
    ctx.rel("Skill", a[0], "DEPENDS_ON", "Skill", b[0])
    for props in (a[1], b[1]):
        topic = prop_str(props, "topicId")
        if topic:
            ctx.dirty_topic(topic)


def _structure_proposed(ctx: _Ctx, e: p.StructureProposed) -> None:
    domain_id = str(e.domain_id)
    _set_domain(ctx, e.domain_id, {"proposedVersion": e.structure_version})
    for topic in e.topics:
        _upsert_topic_draft(ctx, domain_id, topic, "proposed")
    for skill in e.skills:
        _upsert_skill_draft(ctx, domain_id, skill, "proposed")
    for edge in e.prereqs:
        _add_prereq(ctx, edge)
    for dep in e.skill_deps:
        _add_skill_dep(ctx, dep)
    for draft in e.misconceptions:
        skill = ctx.node("Skill", draft.skill_id)
        if skill is None or ctx.node("Misconception", draft.misconception_id) is not None:
            continue
        _create_misconception(
            ctx,
            str(draft.misconception_id),
            domain_id,
            skill[0],
            draft.name,
            draft.description or "",
            "structure",
        )


def _structure_confirmed(ctx: _Ctx, e: p.StructureConfirmed) -> None:
    domain_id = str(e.domain_id)
    _set_domain(ctx, e.domain_id, {"status": "active", "structureVersion": e.structure_version})
    for label in ("Topic", "Skill"):
        for node_id, props in ctx.view.graph.nodes_with_label(label):
            if props.get("domainId") == domain_id and props.get("status") == "proposed":
                ctx.set(label, node_id, {"status": "active"})
                if label == "Topic":
                    ctx.dirty_topic(node_id)
                else:
                    ctx.dirty_skill(node_id)


def _structure_edited(ctx: _Ctx, e: p.StructureEdited) -> None:
    domain_id = str(e.domain_id)
    status = "active" if _domain_status(ctx, domain_id) == "active" else "proposed"
    _set_domain(ctx, e.domain_id, {"structureVersion": e.structure_version})
    for topic in e.added_topics:
        _upsert_topic_draft(ctx, domain_id, topic, status)
    for patch in e.updated_topics:
        found = ctx.node("Topic", patch.topic_id)
        if found is None:
            continue
        props: Props = {}
        if patch.name is not None:
            props.update({"name": patch.name, "normName": normalize_name(patch.name)})
        if patch.description is not None:
            props["description"] = patch.description
        if props:
            ctx.set("Topic", found[0], props)
        ctx.dirty_topic(found[0])
    for topic_id in e.archived_topic_ids:
        found = ctx.node("Topic", topic_id)
        if found is not None:
            ctx.set("Topic", found[0], {"status": "archived"})
            ctx.dirty_topic(found[0])
    for edge in e.added_prereqs:
        _add_prereq(ctx, edge)
    for edge in e.removed_prereqs:
        _remove_prereq(ctx, edge)
    for skill in e.added_skills:
        _upsert_skill_draft(ctx, domain_id, skill, status)
    for patch in e.updated_skills:
        found = ctx.node("Skill", patch.skill_id)
        if found is None:
            continue
        skill_id, skill_props = found
        props = {}
        if patch.name is not None:
            props.update({"name": patch.name, "normName": normalize_name(patch.name)})
        if patch.description is not None:
            props["description"] = patch.description
        if props:
            ctx.set("Skill", skill_id, props)
        if patch.topic_id is not None:
            topic = ctx.node("Topic", patch.topic_id)
            if topic is not None:
                _set_skill_topic(ctx, skill_id, prop_str(skill_props, "topicId"), topic[0])
        ctx.dirty_skill(skill_id)
    for skill_id in e.archived_skill_ids:
        found = ctx.node("Skill", skill_id)
        if found is not None:
            ctx.set("Skill", found[0], {"status": "archived"})
            ctx.dirty_skill(found[0])


def _topic_status_changed(ctx: _Ctx, e: p.TopicStatusChanged) -> None:
    found = ctx.node("Topic", e.topic_id)
    if found is not None:
        ctx.set("Topic", found[0], {"status": e.status})
        ctx.dirty_topic(found[0])


def _topic_merged(ctx: _Ctx, e: p.TopicMerged) -> None:
    pair = _merge(ctx, "Topic", e.source_topic_id, e.target_topic_id)
    if pair is None:
        return
    src, tgt = pair
    graph = ctx.view.graph
    for topic in (src, tgt):
        for key in graph.in_rels("Topic", topic, "PREREQ_OF") + graph.out_rels(
            "Topic", topic, "PREREQ_OF"
        ):
            ctx.dirty_topic(key[1])
            ctx.dirty_topic(key[4])
    for skill_id, props in graph.nodes_with_label("Skill"):
        if props.get("topicId") == src:
            ctx.set("Skill", skill_id, {"topicId": tgt})
    ctx.emit(RebindNodeRel("Topic", src, tgt, ctx.iso))
    ctx.set("Topic", src, dict(EMPTY_TOPIC_DISPLAY))
    ctx.result.dirty_topic_ids.discard(src)
    ctx.dirty_topic(tgt)


def _skill_promoted(ctx: _Ctx, e: p.SkillPromoted) -> None:
    found = ctx.node("Skill", e.skill_id)
    if found is None:
        return
    skill_id, props = found
    ctx.set(
        "Skill",
        skill_id,
        {
            "status": "active",
            "name": e.name,
            "normName": normalize_name(e.name),
            "description": e.description or "",
        },
    )
    topic = ctx.node("Topic", e.topic_id)
    if topic is not None:
        _set_skill_topic(ctx, skill_id, prop_str(props, "topicId"), topic[0])
    ctx.dirty_skill(skill_id)


def _skill_merged(ctx: _Ctx, e: p.SkillMerged) -> None:
    pair = _merge(ctx, "Skill", e.source_skill_id, e.target_skill_id)
    if pair is None:
        return
    src, tgt = pair
    graph = ctx.view.graph
    ctx.dirty_skill(src)
    ctx.dirty_skill(tgt)
    for misc_id, props in graph.nodes_with_label("Misconception"):
        if props.get("skillId") == src:
            ctx.set("Misconception", misc_id, {"skillId": tgt})
    ctx.emit(RebindNodeRel("Skill", src, tgt, ctx.iso))
    ctx.set("Skill", src, dict(EMPTY_SKILL_FOLD))
    ctx.result.dirty_skill_ids.discard(src)
    ctx.dirty_skill(tgt)


def _misconception_merged(ctx: _Ctx, e: p.MisconceptionMerged) -> None:
    pair = _merge(ctx, "Misconception", e.source_misconception_id, e.target_misconception_id)
    if pair is None:
        return
    src, tgt = pair
    ctx.emit(RebindNodeRel("Misconception", src, tgt, ctx.iso))
    ctx.set("Misconception", src, dict(EMPTY_MISCONCEPTION_FOLD) | {"status": "merged"})
    ctx.result.dirty_misconception_ids.discard(src)
    ctx.result.dirty_misconception_ids.add(tgt)


def _misconception_resolved(ctx: _Ctx, e: p.MisconceptionResolved) -> None:
    found = ctx.node("Misconception", e.misconception_id)
    if found is None:
        return
    ctx.set("Misconception", found[0], {"resolvedAt": ctx.at, "resolvedSeq": ctx.seq})
    ctx.result.dirty_misconception_ids.add(found[0])


def _question_answered(ctx: _Ctx, e: p.DiagnosticQuestionAnswered) -> None:
    skill = ctx.node("Skill", e.skill_id)
    if skill is None:
        return
    task_id, family = _task_family(ctx, e.task_id)
    _evidence(
        ctx,
        0,
        kind="skill",
        target_label="Skill",
        target_id=skill[0],
        rel_type="EVIDENCE_FOR",
        domain_id=prop_str(skill[1], "domainId"),
        tier=DIAGNOSTIC_TIER,
        source_type="diagnostic",
        score=e.score,
        tested_depth=e.tested_depth,
        family_id=family,
        task_id=task_id,
    )
    ctx.dirty_skill(skill[0])


def _diagnostic_completed(ctx: _Ctx, e: p.DiagnosticCompleted) -> None:
    for prior in e.inferred_priors:
        skill = ctx.node("Skill", prior.skill_id)
        if skill is None:
            continue
        ctx.set(
            "Skill",
            skill[0],
            {
                "priorMastery": prior.prior_mastery,
                "priorConfidence": prior.confidence,
                "priorSource": prior.source,
            },
        )
        ctx.dirty_skill(skill[0])


def _concept_explained(ctx: _Ctx, e: p.TeacherConceptExplained) -> None:
    skills: list[str] = []
    for ref in e.skills:
        skill_id = _resolve_skill_ref(ctx, ref)
        if skill_id is not None and skill_id not in skills:
            skills.append(skill_id)
    if not skills:
        return
    topic = ctx.node("Topic", e.topic_id)
    exposure_id = ctx.item_id(0)
    ctx.set(
        "Exposure",
        exposure_id,
        {
            "learnerId": ctx.learner,
            "domainId": prop_str(topic[1], "domainId") if topic else _skill_domain(ctx, skills[0]),
            "topicId": topic[0] if topic else "",
            "sessionId": str(e.session_id),
            "turnId": str(e.turn_id),
            "at": ctx.at,
            "depth": e.depth,
            "sourceSeq": ctx.seq,
        },
    )
    for skill_id in skills:
        ctx.rel("Exposure", exposure_id, "EXPOSED_TO", "Skill", skill_id)
        ctx.dirty_skill(skill_id)


# `turn.extracted` v2 (WP12): the outcome of an element → score and direction of the trace.
OUTCOME_SCORE = {"correct": 1.0, "incorrect": 0.0, "partial": 0.5}
OUTCOME_DIRECTION = {"correct": "positive", "incorrect": "negative", "partial": "neutral"}


def _turn_extracted(ctx: _Ctx, e: p.TurnExtracted | p.TurnExtractedV2) -> None:
    if isinstance(e, p.TurnExtractedV2):
        _turn_extracted_v2(ctx, e)
        return
    for i, obs in enumerate(e.observations):
        skill_id = _resolve_skill_ref(ctx, obs.skill)
        if skill_id is None:
            continue
        _evidence(
            ctx,
            i,
            kind="skill",
            target_label="Skill",
            target_id=skill_id,
            rel_type="EVIDENCE_FOR",
            domain_id=_skill_domain(ctx, skill_id),
            tier=EXTRACTOR_TIER,
            source_type="extractor",
            score=DIRECTION_SCORE[obs.direction],
            independence=obs.independence,
            tested_depth=obs.tested_depth,
            strength=obs.strength,
            polarity_weight=obs.polarity_weight,
            direction=obs.direction,
        )
        ctx.dirty_skill(skill_id)
    for j, misc in enumerate(e.misconceptions):
        skill_id = _resolve_skill_ref(ctx, misc.skill)
        if skill_id is None:
            continue
        misc_id = _misconception_for(ctx, misc.name, skill_id, None, f"misc:{j}", "extracted")
        _evidence(
            ctx,
            f"manifest:{j}",
            kind="manifestation",
            target_label="Misconception",
            target_id=misc_id,
            rel_type="OBSERVED_MISCONCEPTION",
            domain_id=_skill_domain(ctx, skill_id),
            tier=EXTRACTOR_TIER,
            source_type="extractor",
            score=0.0,
            strength=misc.confidence,
            direction="negative",
        )
        ctx.result.dirty_misconception_ids.add(misc_id)


def _turn_extracted_v2(ctx: _Ctx, e: p.TurnExtractedV2) -> None:
    """Elements of `extractor@2` (WP12.2, law 4): the tier is T3 by source (`Т-§7.8` п.4), the
    time comes from the source events, and no number of the model reaches the trace. An element
    rejected by code validation stays in the event and is not applied."""
    for i, el in enumerate(e.elements):
        if el.rejected_reason is not None or el.skill is None:
            continue
        skill_id = _resolve_skill_ref(ctx, el.skill)
        if skill_id is None:
            continue
        at_ms = to_ms(el.occurred_at) if el.occurred_at is not None else None
        if el.kind == "misconception_hypothesis":
            name = el.misconception_label or ""
            misc_id = _misconception_for(
                ctx, name, skill_id, el.misconception_id, f"misc:{i}", "extracted"
            )
            _evidence(
                ctx,
                f"manifest:{i}",
                kind="manifestation",
                target_label="Misconception",
                target_id=misc_id,
                rel_type="OBSERVED_MISCONCEPTION",
                domain_id=_skill_domain(ctx, skill_id),
                tier=EXTRACTOR_TIER,
                source_type="extractor",
                score=0.0,
                direction="negative",
                at_ms=at_ms,
            )
            ctx.result.dirty_misconception_ids.add(misc_id)
            continue
        if el.outcome is None or el.level is None or el.assistance is None:
            continue  # incomplete: code validation marks it, the projector never stops on it
        _evidence(
            ctx,
            i,
            kind="skill",
            target_label="Skill",
            target_id=skill_id,
            rel_type="EVIDENCE_FOR",
            domain_id=_skill_domain(ctx, skill_id),
            tier=EXTRACTOR_TIER,
            source_type="extractor",
            score=OUTCOME_SCORE[el.outcome],
            direction=OUTCOME_DIRECTION[el.outcome],
            at_ms=at_ms,
            extra={
                "level": el.level,
                "assistance": el.assistance,
                "hintStep": el.hint_step or 0,
                "gapType": el.gap_type or "unknown",
            },
        )
        ctx.dirty_skill(skill_id)


def _task_generated(ctx: _Ctx, e: p.TaskGenerated) -> None:
    task_id = str(e.task_id)
    topic = ctx.node("Topic", e.topic_id)
    domain_id = str(e.domain_id)
    ctx.set(
        "Task",
        task_id,
        {
            "learnerId": ctx.learner,
            "domainId": domain_id,
            "topicId": topic[0] if topic else ctx.view.resolve(str(e.topic_id)),
            "familyId": e.family_id,
            "difficulty": e.difficulty,
            "kind": e.kind,
            "status": "generated",
            "assignedSessionId": "",
            "assignedTurnId": "",
            "hintsGiven": 0,
            "maxHintLevel": 0,
            "hintsUsed": 0,
            "evaluator": "unknown",
            "lastScore": 0.0,
            "lastIsCorrect": False,
            "evaluations": 0,
            "createdSeq": ctx.seq,
        },
    )
    for target in e.target_skills:
        skill_id = _resolve_skill_ref(ctx, target.skill)
        if skill_id is not None:
            ctx.rel("Task", task_id, "TESTS", "Skill", skill_id, {"weight": target.weight})
    for name in e.target_misconceptions:
        misc_id = _find_by_norm_name(ctx, "Misconception", domain_id, name)
        if misc_id is not None:
            ctx.rel("Task", task_id, "TRIGGERS_MISCONCEPTION", "Misconception", misc_id)


def _task_assigned(ctx: _Ctx, e: p.TaskAssigned) -> None:
    found = ctx.node("Task", e.task_id)
    if found is not None:
        ctx.set(
            "Task",
            found[0],
            {
                "status": "assigned",
                "assignedSessionId": str(e.session_id),
                "assignedTurnId": str(e.turn_id),
            },
        )


def _hint_given(ctx: _Ctx, e: p.TaskHintGiven) -> None:
    found = ctx.node("Task", e.task_id)
    if found is not None:
        task_id, props = found
        ctx.set(
            "Task",
            task_id,
            {
                "hintsGiven": prop_int(props, "hintsGiven") + 1,
                "maxHintLevel": max(prop_int(props, "maxHintLevel"), e.hint_level),
            },
        )


def _task_submitted(ctx: _Ctx, e: p.TaskSubmitted) -> None:
    found = ctx.node("Task", e.task_id)
    if found is not None:
        ctx.set("Task", found[0], {"status": "submitted", "hintsUsed": e.hints_used})


def _task_evaluated(ctx: _Ctx, e: p.TaskEvaluated) -> None:
    task = ctx.node("Task", e.task_id)
    task_id, family = (
        (task[0], prop_str(task[1], "familyId", "unknown")) if task else ("", "unknown")
    )
    if task is not None:
        ctx.set(
            "Task",
            task[0],
            {
                "status": "evaluated",
                "evaluator": e.evaluator,
                "lastScore": e.score,
                "lastIsCorrect": e.is_correct,
                "evaluations": prop_int(task[1], "evaluations") + 1,
            },
        )
    tier = EVALUATOR_TIER[e.evaluator]
    for i, item in enumerate(e.skill_scores):
        skill = ctx.node("Skill", item.skill_id)
        if skill is None:
            continue
        _evidence(
            ctx,
            i,
            kind="skill",
            target_label="Skill",
            target_id=skill[0],
            rel_type="EVIDENCE_FOR",
            domain_id=prop_str(skill[1], "domainId"),
            tier=tier,
            source_type="task",
            score=item.score,
            independence=e.independence,
            tested_depth=item.tested_depth,
            family_id=family,
            task_id=task_id,
        )
        ctx.dirty_skill(skill[0])
    for j, hit in enumerate(e.triggered_misconceptions):
        skill = ctx.node("Skill", hit.skill_id)
        if skill is None:
            continue
        misc_id = _misconception_for(
            ctx, hit.name, skill[0], hit.misconception_id, f"misc:{j}", "evaluated"
        )
        _evidence(
            ctx,
            f"hit:{j}",
            kind="manifestation",
            target_label="Misconception",
            target_id=misc_id,
            rel_type="OBSERVED_MISCONCEPTION",
            domain_id=prop_str(skill[1], "domainId"),
            tier=tier,
            source_type="task",
            score=0.0,
            independence=e.independence,
            direction="negative",
            family_id=family,
            task_id=task_id,
        )
        ctx.result.dirty_misconception_ids.add(misc_id)


def _evidence_superseded(ctx: _Ctx, e: p.EvidenceSuperseded) -> None:
    only = {str(x) for x in e.evidence_ids}
    graph = ctx.view.graph
    for evidence_id, props in graph.nodes_with_label("Evidence"):
        if props.get("sourceSeq") != e.target_event_seq or props.get("superseded") is True:
            continue
        if only and evidence_id not in only:
            continue
        ctx.set("Evidence", evidence_id, {"superseded": True, "supersededBySeq": ctx.seq})
        for key in graph.out_rels("Evidence", evidence_id, "EVIDENCE_FOR"):
            ctx.dirty_skill(key[4])
        for key in graph.out_rels("Evidence", evidence_id, "OBSERVED_MISCONCEPTION"):
            ctx.result.dirty_misconception_ids.add(key[4])


def _artifact(
    ctx: _Ctx,
    artifact_id: UUID,
    domain_id: UUID,
    topic_id: UUID | None,
    session_id: UUID | None,
    kind: str,
    title: str,
    version: int,
) -> None:
    node_id = str(artifact_id)
    topic = ctx.node("Topic", topic_id)
    domain = ctx.node("Domain", domain_id)
    ctx.set(
        "Artifact",
        node_id,
        {
            "learnerId": ctx.learner,
            "domainId": str(domain_id),
            "topicId": topic[0] if topic else "",
            "sessionId": str(session_id) if session_id else "",
            "kind": kind,
            "title": title,
            "version": version,
            "isDeleted": False,
            "sourceSeq": ctx.seq,
        },
    )
    if topic is not None:
        ctx.rel("Topic", topic[0], "HAS_ARTIFACT", "Artifact", node_id)
    elif domain is not None:
        ctx.rel("Domain", domain[0], "HAS_ARTIFACT", "Artifact", node_id)


def _digest_created(ctx: _Ctx, e: p.SessionDigestCreated) -> None:
    _artifact(ctx, e.artifact_id, e.domain_id, e.topic_id, e.session_id, "digest", e.title, 1)


def _note_created(ctx: _Ctx, e: p.ArtifactNoteCreated) -> None:
    _artifact(ctx, e.artifact_id, e.domain_id, e.topic_id, e.session_id, "note", e.title, 1)


def _note_updated(ctx: _Ctx, e: p.ArtifactNoteUpdated) -> None:
    found = ctx.node("Artifact", e.artifact_id)
    if found is not None:
        ctx.set(
            "Artifact", found[0], {"title": e.title, "version": e.version, "sourceSeq": ctx.seq}
        )


def _note_deleted(ctx: _Ctx, e: p.ArtifactNoteDeleted) -> None:
    found = ctx.node("Artifact", e.artifact_id)
    if found is not None:
        ctx.set("Artifact", found[0], {"isDeleted": True, "sourceSeq": ctx.seq})


def _cheatsheet_updated(ctx: _Ctx, e: p.ArtifactCheatsheetUpdated) -> None:
    found = ctx.node("Artifact", e.artifact_id)
    if found is None:
        _artifact(
            ctx, e.artifact_id, e.domain_id, e.topic_id, None, "cheatsheet", e.title, e.version
        )
    else:
        ctx.set(
            "Artifact", found[0], {"title": e.title, "version": e.version, "sourceSeq": ctx.seq}
        )


type _Handler = Callable[[_Ctx, p.Payload], None]

HANDLERS: dict[str, Callable[[_Ctx, p.Payload], None]] = {
    "learner.registered": _learner_registered,  # type: ignore[dict-item]
    "learner.preferences_updated": _no_graph_effect,
    "domain.created": _domain_created,  # type: ignore[dict-item]
    "domain.archived": _domain_archived,  # type: ignore[dict-item]
    "structure.interview_completed": _interview_completed,  # type: ignore[dict-item]
    "structure.proposed": _structure_proposed,  # type: ignore[dict-item]
    "structure.confirmed": _structure_confirmed,  # type: ignore[dict-item]
    "structure.edited": _structure_edited,  # type: ignore[dict-item]
    "topic.status_changed": _topic_status_changed,  # type: ignore[dict-item]
    "topic.merged": _topic_merged,  # type: ignore[dict-item]
    "skill.promoted": _skill_promoted,  # type: ignore[dict-item]
    "skill.merged": _skill_merged,  # type: ignore[dict-item]
    "diagnostic.started": _no_graph_effect,
    "diagnostic.question_answered": _question_answered,  # type: ignore[dict-item]
    "diagnostic.completed": _diagnostic_completed,  # type: ignore[dict-item]
    "session.started": _no_graph_effect,
    "session.completed": _no_graph_effect,
    "session.abandoned": _no_graph_effect,
    "session.checkpointed": _no_graph_effect,
    "turn.student_replied": _no_graph_effect,
    "turn.teacher_replied": _no_graph_effect,
    "teacher.concept_explained": _concept_explained,  # type: ignore[dict-item]
    "turn.extracted": _turn_extracted,  # type: ignore[dict-item]
    "task.generated": _task_generated,  # type: ignore[dict-item]
    "task.assigned": _task_assigned,  # type: ignore[dict-item]
    "task.hint_given": _hint_given,  # type: ignore[dict-item]
    "task.submitted": _task_submitted,  # type: ignore[dict-item]
    "task.evaluated": _task_evaluated,  # type: ignore[dict-item]
    "misconception.resolved": _misconception_resolved,  # type: ignore[dict-item]
    "misconception.merged": _misconception_merged,  # type: ignore[dict-item]
    "evidence.superseded": _evidence_superseded,  # type: ignore[dict-item]
    "session.digest_created": _digest_created,  # type: ignore[dict-item]
    "artifact.note_created": _note_created,  # type: ignore[dict-item]
    "artifact.note_updated": _note_updated,  # type: ignore[dict-item]
    "artifact.note_deleted": _note_deleted,  # type: ignore[dict-item]
    "artifact.cheatsheet_updated": _cheatsheet_updated,  # type: ignore[dict-item]
}


def apply_event_to_trace(event: StoredEvent, view: BatchView) -> PhaseAResult:
    """Pure handler: resolves ids via Alias, emits mutations into `view`, returns dirty sets.

    An unknown type or schema version raises (the learner stream stops, Т-§2.4).
    """
    payload = event.parsed()
    handler = HANDLERS.get(event.type)
    if handler is None:
        raise ValueError(f"no projector handler for {event.type!r}")
    result = PhaseAResult()
    handler(_Ctx(event=event, view=view, result=result), payload)
    return result
