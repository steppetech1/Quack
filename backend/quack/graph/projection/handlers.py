"""Handlers of phase A, `rules@3` (ТЗ Ф4 WP3.2; Т-§2.4, Т-§2.5).

One handler per row of the S5 table (`quack.graph.projector_rows.ROWS`): `needs(event)` — the
slice it reads; `handle(event, view)` — pure, no I/O and no current time, returns the changes.
Ids of new nodes are UUIDv5 of `(learner_id, seq, n)` (`quack.graph.ids.deterministic_id`).
"""

from __future__ import annotations

import base64
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from quack.events.event_types import (
    AnswerSubmitted,
    ChunkIndexed,
    DigestWritten,
    DisputeFiled,
    DomainCreated,
    EdgeAdded,
    EdgeRemoved,
    EmbeddingReady,
    HubCreated,
    HubDeadlineSet,
    HubTopicsChanged,
    KnownClaimed,
    MaterialParsed,
    MaterialUploaded,
    MisconceptionDescribed,
    ObservationDiagnosed,
    ObservationExtracted,
    ObservationSecondOpinion,
    PreferenceSet,
    SessionEnded,
    SkillCreated,
    SkillSplit,
    TaskClosed,
    TaskCreated,
    TaskKeyDisputed,
    TaskMarkupDisputed,
    TaskVerified,
    TopicCreated,
    TopicHidden,
    TopicProposed,
    TopicRenamed,
    TopicSplit,
    TurnCommitted,
    TypoDeclared,
)
from quack.events.log import StoredEvent
from quack.events.records import TaskMarkupRecord, TestedLevelRecord
from quack.events.registry import SPECS, EventPayload, payload_model
from quack.events.values import NewSkill, Verdict
from quack.graph.ids import deterministic_id
from quack.graph.projection.mutations import (
    DeleteNode,
    DeleteRel,
    MergeNode,
    MergeRel,
    Mutation,
    SetProps,
)
from quack.graph.projection.types import REL_IDENTITY, Props, RelRecord
from quack.graph.projection.view import NOTHING, Need, SliceView

type Needs = Callable[[StoredEvent], Need]
type Handle = Callable[[StoredEvent, SliceView], list[Mutation]]


@dataclass(frozen=True)
class Handler:
    needs: Needs
    handle: Handle


def handler_for(event: StoredEvent) -> Handler | None:
    """The handler of the event type.

    `None` — a catalog type marked «без обработчика»: the graph does not change, the watermark
    moves (Т-§2.4). An unknown type or a newer schema version than the code knows raises
    `UnknownEventTypeError`: the learner's stream stops (Т-§2.4, T-P6).
    """
    payload_model(event.type, event.schema_version)
    if not SPECS[(event.type, event.schema_version)].projector:
        return None
    return HANDLERS[event.type]


def _payload[P: EventPayload](event: StoredEvent, model: type[P]) -> P:
    return model.model_validate(event.payload)


def _nodes(*refs: tuple[str, str]) -> Need:
    return Need(nodes=frozenset(refs))


def _ref(label: str, field: str) -> Needs:
    """`needs` of a handler that reads one node named by a payload field."""
    return lambda event: _nodes((label, str(event.payload[field])))


def _none(event: StoredEvent) -> Need:
    return NOTHING


# --- structure (Т-§4) -------------------------------------------------------------------------
# Domain and Hub statuses: «active» — the catalog has no event that changes them (D-A-129).


def _domain_created(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§4.8: the hub «весь домен» is created with the domain; its composition is not stored
    (the hub covers every topic of the domain). Origin «learner», control type «no_goal»
    (D-A-129)."""
    p = _payload(event, DomainCreated)
    learner, domain = str(event.learner_id), str(p.domain_id)
    hub = deterministic_id(event.learner_id, event.seq, 0)
    return [
        MergeNode("Learner", learner, {"learnerId": learner}),
        MergeNode("Domain", domain, {"learnerId": learner, "name": p.name, "status": "active"}),
        MergeRel("Learner", learner, "OWNS", "Domain", domain, {}),
        MergeNode(
            "Hub",
            hub,
            {
                "learnerId": learner,
                "domainId": domain,
                "name": p.name,
                "kind": "domain_all",
                "origin": "learner",
                "controlType": "no_goal",
                "deadlineAtMs": 0,
                "status": "active",
            },
        ),
        MergeRel("Domain", domain, "HAS_HUB", "Hub", hub, {}),
    ]


def _topic_new_needs(event: StoredEvent) -> Need:
    p = _payload(event, TopicCreated if event.type == "topic.created" else TopicProposed)
    return _nodes(("Domain", str(p.domain_id)), ("Topic", str(p.topic_id)))


def _topic_new(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """`topic.proposed` / `topic.created` (Т-§4.3, Т-§4.7). A proposed topic that is created
    later keeps what it had (`knownClaimedAtMs`); only name, origin and status change."""
    p = _payload(event, TopicCreated if event.type == "topic.created" else TopicProposed)
    domain, topic = str(p.domain_id), str(p.topic_id)
    if view.node("Domain", domain) is None:
        return []
    status = "active" if event.type == "topic.created" else "proposed"
    props: Props = {"name": p.name, "origin": p.origin, "status": status}
    live = view.resolve("Topic", topic)
    if live is not None:
        return [SetProps("Topic", live, props)]
    learner = str(event.learner_id)
    base: Props = {"learnerId": learner, "domainId": domain, "knownClaimedAtMs": 0}
    return [
        MergeNode("Topic", topic, base | props),
        MergeRel("Domain", domain, "HAS_TOPIC", "Topic", topic, {}),
    ]


def _topic_set(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """`topic.renamed` — the name; `topic.hidden` — status `hidden` (Т-§4.11)."""
    props: Props
    if event.type == "topic.renamed":
        renamed = _payload(event, TopicRenamed)
        topic = str(renamed.topic_id)
        props = {"name": renamed.name}
    else:
        topic = str(_payload(event, TopicHidden).topic_id)
        props = {"status": "hidden"}
    live = view.resolve("Topic", topic)
    return [] if live is None else [SetProps("Topic", live, props)]


def _skill_created(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§4.1, Т-§4.4: the skill and `COVERS` from its home topic. The state of the skill
    (`Т-§3.4.4`) is written by its fold in phase B."""
    p = _payload(event, SkillCreated)
    topic = view.resolve("Topic", str(p.topic_id))
    if topic is None:
        return []
    learner, skill = str(event.learner_id), str(p.skill_id)
    return [
        MergeNode(
            "Skill",
            skill,
            {
                "learnerId": learner,
                "domainId": str(p.domain_id),
                "name": p.name,
                "summary": p.summary,
                "status": p.status,
                "origin": p.origin,
            },
        ),
        MergeRel("Topic", topic, "COVERS", "Skill", skill, {}),
    ]


_EDGE_TYPES = {"requires": "REQUIRES", "same_idea": "SAME_IDEA"}


def _edge_needs(event: StoredEvent) -> Need:
    """The two skills with their evidence: a removed relation supersedes what was derived over
    it (WP4.6)."""
    ends = {("Skill", str(event.payload["from_id"])), ("Skill", str(event.payload["to_id"]))}
    return Need(nodes=frozenset(ends), neighbourhoods=frozenset(ends))


def _edge(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """`REQUIRES {origin, confidence}` or `SAME_IDEA {confidence}` between skills (Т-§4.6). On
    removal the evidence derived over the relation becomes `superseded` — with the derived
    evidence of WP4.6."""
    p = _payload(event, EdgeAdded if event.type == "edge.added" else EdgeRemoved)
    a, b = view.resolve("Skill", str(p.from_id)), view.resolve("Skill", str(p.to_id))
    if a is None or b is None or a == b:
        return []
    rel_type = _EDGE_TYPES[p.kind]
    if not isinstance(p, EdgeAdded):
        # `SAME_IDEA` is stored one way and read without a direction (Т-§3.4.3): removed whichever
        # way it was written.
        ends = [(a, b), (b, a)] if p.kind == "same_idea" else [(a, b)]
        return [
            *(DeleteRel("Skill", x, rel_type, "Skill", y) for x, y in ends),
            *_supersede_derived(event, view, p.kind, a, b),
        ]
    props: Props = {"confidence": p.confidence}
    if p.kind == "requires":
        props["origin"] = p.origin
    return [MergeRel("Skill", a, rel_type, "Skill", b, props)]


def _supersede_derived(
    event: StoredEvent, view: SliceView, kind: str, a: str, b: str
) -> list[Mutation]:
    """WP4.6 [РЕШЕНИЕ Ф4]: a removed relation makes the evidence derived over it `superseded` —
    `indirect` from the evidence of `a` to `b` over `REQUIRES`, `transfer` either way over
    `SAME_IDEA`. The id of a derived evidence names its source, kind and target (D-A-185)."""
    derived = "indirect" if kind == "requires" else "transfer"
    pairs = [(a, b)] if kind == "requires" else [(a, b), (b, a)]
    changes: list[Mutation] = []
    for source_skill, target in pairs:
        on_target = {r.from_id for r in view.in_rels("Skill", target, "ON")}
        for r in view.in_rels("Skill", source_skill, "ON"):
            node = _derived_id(event, r.from_id, derived, target)
            props = view.node("Evidence", node) if node in on_target else None
            if props is not None and props.get("status") == "active":
                changes.append(SetProps("Evidence", node, {"status": "superseded"}))
    return changes


def _derived_id(event: StoredEvent, source: str, kind: str, target: str) -> str:
    return deterministic_id(event.learner_id, 0, f"derived:{source}:{kind}:{target}")


def _hub_created(event: StoredEvent, view: SliceView) -> list[Mutation]:
    p = _payload(event, HubCreated)
    domain, hub = str(p.domain_id), str(p.hub_id)
    if view.node("Domain", domain) is None:
        return []
    return [
        MergeNode(
            "Hub",
            hub,
            {
                "learnerId": str(event.learner_id),
                "domainId": domain,
                "name": p.name,
                "kind": p.kind,
                "origin": p.origin,
                "controlType": p.control_type,
                "deadlineAtMs": 0,
                "status": "active",
            },
        ),
        MergeRel("Domain", domain, "HAS_HUB", "Hub", hub, {}),
    ]


def _hub_deadline_set(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """A removed deadline is 0, «не было» (`Т-§3.4.4`, law 8)."""
    p = _payload(event, HubDeadlineSet)
    hub = view.resolve("Hub", str(p.hub_id))
    deadline = p.deadline_at_ms if p.deadline_at_ms is not None else 0
    return [] if hub is None else [SetProps("Hub", hub, {"deadlineAtMs": deadline})]


def _hub_topics_needs(event: StoredEvent) -> Need:
    p = _payload(event, HubTopicsChanged)
    return Need(
        nodes=frozenset(("Topic", str(t.topic_id)) for t in p.topics),
        neighbourhoods=frozenset({("Hub", str(p.hub_id))}),
    )


def _hub_topics_changed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """The event gives the whole set of topics of the hub with their required levels (Т-§4.8,
    Т-§8.5); topics not in it leave the hub. The default levels by `controlType` — WP6.3."""
    p = _payload(event, HubTopicsChanged)
    hub = str(p.hub_id)
    if view.node("Hub", hub) is None:
        return []
    wanted: dict[str, list[int]] = {}
    for item in p.topics:
        topic = view.resolve("Topic", str(item.topic_id))
        if topic is not None:
            wanted[topic] = list(item.required_levels)
    changes: list[Mutation] = [
        DeleteRel("Hub", hub, "INCLUDES", "Topic", r.to_id)
        for r in view.out_rels("Hub", hub, "INCLUDES")
        if r.to_id not in wanted
    ]
    changes += [
        MergeRel("Hub", hub, "INCLUDES", "Topic", topic, {"requiredLevels": levels})
        for topic, levels in wanted.items()
    ]
    return changes


# --- vectors (Т-§1.7, Т-§2.4) ----------------------------------------------------------------

_VECTOR_LABELS = {
    "evidence": "Evidence",
    "misconception": "Misconception",
    "chunk": "Chunk",
    "skill": "Skill",
    "topic": "Topic",
}


def _embedding_needs(event: StoredEvent) -> Need:
    return _nodes((_VECTOR_LABELS[event.payload["node_kind"]], str(event.payload["node_id"])))


def _embedding_ready(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """The vector on the node through its aliases; no node — nothing changes (Т-§2.4, T-P5).
    `vector` — base64 of float32 little-endian (ТЗ Ф4 WP20.2; byte order D-A-129)."""
    p = _payload(event, EmbeddingReady)
    label = _VECTOR_LABELS[p.node_kind]
    node = view.resolve(label, str(p.node_id))
    if node is None:
        return []
    raw = base64.b64decode(p.vector)
    vector = list(struct.unpack(f"<{len(raw) // 4}f", raw))
    return [SetProps(label, node, {"embedding": vector})]


# --- learner edits, profile, materials, digest, misconceptions --------------------------------


def _ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def _id(value: UUID | None) -> str:
    """An optional id as a property: no id is the explicit `''` (law 8, D-A-130)."""
    return "" if value is None else str(value)


def _profile_id(event: StoredEvent) -> str:
    """The only Profile of the learner (`Т-§3.4.3`, 1:1): UUIDv5 of the learner (D-A-130)."""
    return deterministic_id(event.learner_id, 0, "profile")


def _digest_id(event: StoredEvent, domain_id: str) -> str:
    """The latest digest of a domain (`Т-§3.4.3`, 1:1): UUIDv5 of the learner and domain."""
    return deterministic_id(event.learner_id, 0, f"digest:{domain_id}")


def _profile(event: StoredEvent, props: Props) -> list[Mutation]:
    learner, profile = str(event.learner_id), _profile_id(event)
    return [
        MergeNode("Learner", learner, {"learnerId": learner}),
        MergeNode("Profile", profile, {"learnerId": learner} | props),
        MergeRel("Learner", learner, "HAS_PROFILE", "Profile", profile, {}),
    ]


def _known_claimed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§4.11: the learner says «знаю»; the state does not change (the check is a job)."""
    topic = view.resolve("Topic", str(_payload(event, KnownClaimed).topic_id))
    return (
        []
        if topic is None
        else [SetProps("Topic", topic, {"knownClaimedAtMs": _ms(event.occurred_at)})]
    )


def _camel(key: str) -> str:
    return "".join(part.capitalize() for part in key.split("_"))


def _preference_set(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§9.5: `pref*` — key `language` is `prefLanguage` (WP5.3)."""
    p = _payload(event, PreferenceSet)
    return _profile(event, {f"pref{_camel(p.key)}": p.value})


def _material_uploaded(event: StoredEvent, view: SliceView) -> list[Mutation]:
    p = _payload(event, MaterialUploaded)
    return [
        MergeNode(
            "Material",
            str(p.material_id),
            {
                "learnerId": str(event.learner_id),
                "domainId": _id(event.domain_id),
                "mime": p.mime,
                "sessionId": _id(p.session_id),
                "pagesCount": 0,
            },
        ),
    ]


def _material_parsed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    p = _payload(event, MaterialParsed)
    material = view.resolve("Material", str(p.material_id))
    return (
        [] if material is None else [SetProps("Material", material, {"pagesCount": p.pages_count})]
    )


def _chunk_needs(event: StoredEvent) -> Need:
    p = _payload(event, ChunkIndexed)
    skills = {("Skill", str(s)) for s in (*p.teaches, *p.uses)}
    return Need(nodes=frozenset({("Material", str(p.material_id)), *skills}))


def _chunk_indexed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """`searchable` — has text (`Т-§3.4.6`); `learnerId`, `domainId` — filter of `chunk_emb`."""
    p = _payload(event, ChunkIndexed)
    material = view.resolve("Material", str(p.material_id))
    if material is None:
        return []
    chunk = str(p.chunk_id)
    changes: list[Mutation] = [
        MergeNode(
            "Chunk",
            chunk,
            {
                "learnerId": str(event.learner_id),
                "domainId": _id(event.domain_id),
                "page": p.page,
                "text": p.text,
                "notation": list(p.notation),
                "searchable": bool(p.text.strip()),
            },
        ),
        MergeRel("Material", material, "HAS_CHUNK", "Chunk", chunk, {}),
    ]
    for rel_type, skills in (("TEACHES", p.teaches), ("USES", p.uses)):
        for skill in skills:
            live = view.resolve("Skill", str(skill))
            if live is not None:
                changes.append(MergeRel("Chunk", chunk, rel_type, "Skill", live, {}))
    return changes


def _digest_written(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """`Т-§13.5`: the latest digest of the domain replaces the earlier one; the teacher manner
    is `Profile.learnedManner` (§6.2, WP5.3)."""
    p = _payload(event, DigestWritten)
    domain = str(p.domain_id)
    if view.node("Domain", domain) is None:
        return []
    digest = _digest_id(event, domain)
    props: Props = {"learnerId": str(event.learner_id), "domainId": domain, "text": p.text,
                    "asOfSeq": p.as_of_seq}  # fmt: skip
    return [
        MergeNode("Digest", digest, props),
        MergeRel("Domain", domain, "HAS_DIGEST", "Digest", digest, {}),
        *_profile(event, {"learnedManner": p.manner}),
    ]


def _misconception_described(event: StoredEvent, view: SliceView) -> list[Mutation]:
    p = _payload(event, MisconceptionDescribed)
    node = view.resolve("Misconception", str(p.misconception_id))
    props: Props = {"statement": p.statement, "triggers": list(p.triggers)}
    return [] if node is None else [SetProps("Misconception", node, props)]


def _session_ended_needs(event: StoredEvent) -> Need:
    if event.domain_id is None:
        return NOTHING
    return Need(domain_labels=frozenset({(str(event.domain_id), "Misconception")}))


def _session_ended(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§9.1: the targeting of the session ends with it; a session has one domain (Т-§22.9)."""
    if event.domain_id is None:
        return []
    session = str(_payload(event, SessionEnded).session_id)
    return [
        SetProps("Misconception", m.id, {"targetedInSessionId": ""})
        for m in view.domain_nodes(str(event.domain_id), "Misconception")
        if m.props.get("targetedInSessionId") == session
    ]


# --- merges and splits (Т-§2.8) ---------------------------------------------------------------

_MERGED = {
    "skill.merged": "Skill",
    "topic.merged": "Topic",
    "misconception.merged": "Misconception",
}
# Relations of which the target keeps its own and the old ones are not moved, `(type, out)`: one
# home topic of a skill, one domain of a topic, one main skill of a misconception (`Т-§3.4.3`).
_OWN_RELS: dict[str, frozenset[tuple[str, bool]]] = {
    "Skill": frozenset({("COVERS", False)}),
    "Topic": frozenset({("HAS_TOPIC", False)}),
    "Misconception": frozenset({("ABOUT", True), ("CONFUSED_WITH", True)}),
}


def _merge_needs(event: StoredEvent) -> Need:
    label = _MERGED[event.type]
    ends = {(label, str(event.payload["from_id"])), (label, str(event.payload["into_id"]))}
    return Need(neighbourhoods=frozenset(ends))


def _identity(r: RelRecord) -> tuple[object, ...]:
    return tuple(r.props.get(name) for name in REL_IDENTITY.get(r.rel_type, ()))


def _merged(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Relations and evidence go to the target, the old node is deleted and its id becomes an
    `Alias` of the target; aliases of the old node move with its other relations (Т-§2.8). A
    relation the target already has, or one that would lead to the target itself, is dropped.
    The target's state is folded again in phase B."""
    label = _MERGED[event.type]
    old_ref, into_ref = str(event.payload["from_id"]), str(event.payload["into_id"])
    old, target = view.resolve(label, old_ref), view.resolve(label, into_ref)
    if old is None or target is None or old == target:
        return []
    own = _OWN_RELS[label]
    # A relation is told apart by its identity props too: `TESTS {level, part}` of the old skill
    # is not the `TESTS` of the same task with another level or part (Т-§3.4.3, D-A-133).
    target_out = {(r.rel_type, r.to_label, r.to_id, *_identity(r))
                  for r in view.out_rels(label, into_ref)}  # fmt: skip
    target_in = {(r.rel_type, r.from_label, r.from_id, *_identity(r))
                 for r in view.in_rels(label, into_ref)}  # fmt: skip
    changes: list[Mutation] = []
    for r in view.out_rels(label, old_ref):
        if (r.rel_type, True) in own or (r.to_label, r.to_id) == (label, target):
            continue
        if (r.rel_type, r.to_label, r.to_id, *_identity(r)) not in target_out:
            changes.append(MergeRel(label, target, r.rel_type, r.to_label, r.to_id, r.props))
    for r in view.in_rels(label, old_ref):
        if (r.rel_type, False) in own or (r.from_label, r.from_id) == (label, target):
            continue
        if (r.rel_type, r.from_label, r.from_id, *_identity(r)) not in target_in:
            changes.append(MergeRel(r.from_label, r.from_id, r.rel_type, label, target, r.props))
    return [
        *changes,
        DeleteNode(label, old),
        MergeNode("Alias", old, {"learnerId": str(event.learner_id)}),
        MergeRel("Alias", old, "ALIAS_OF", label, target, {}),
    ]


def _topic_split_needs(event: StoredEvent) -> Need:
    p = _payload(event, TopicSplit)
    return Need(
        nodes=frozenset(("Topic", str(part.topic_id)) for part in p.into),
        neighbourhoods=frozenset(
            {("Topic", str(p.topic_id))}
            | {("Skill", str(skill)) for part in p.into for skill in part.skill_ids}
        ),
    )


def _topic_split(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Skills are distributed between the parts (Т-§2.8). A part with the old id keeps the
    topic and takes the part's name; a new part is a topic of the same domain with the origin
    and status of the split one. A split topic that is not a part is left without skills and
    hidden (D-A-131)."""
    p = _payload(event, TopicSplit)
    old = view.resolve("Topic", str(p.topic_id))
    old_props = view.node("Topic", str(p.topic_id))
    if old is None or old_props is None:
        return []
    domain = str(old_props["domainId"])
    changes: list[Mutation] = []
    parts: set[str] = set()
    for part in p.into:
        topic = view.resolve("Topic", str(part.topic_id))
        if topic is None:
            topic = str(part.topic_id)
            changes += [
                MergeNode(
                    "Topic",
                    topic,
                    {
                        "learnerId": str(event.learner_id),
                        "domainId": domain,
                        "name": part.name,
                        "origin": old_props["origin"],
                        "status": old_props["status"],
                        "knownClaimedAtMs": 0,
                    },
                ),
                MergeRel("Domain", domain, "HAS_TOPIC", "Topic", topic, {}),
            ]
        else:
            changes.append(SetProps("Topic", topic, {"name": part.name}))
        parts.add(topic)
        for skill_ref in part.skill_ids:
            skill = view.resolve("Skill", str(skill_ref))
            if skill is None:
                continue
            for r in view.in_rels("Skill", str(skill_ref), "COVERS"):
                if r.from_id != topic:
                    changes.append(DeleteRel("Topic", r.from_id, "COVERS", "Skill", skill))
            changes.append(MergeRel("Topic", topic, "COVERS", "Skill", skill, {}))
    if old not in parts:
        changes.append(SetProps("Topic", old, {"status": "hidden"}))
    return changes


def _skill_split_needs(event: StoredEvent) -> Need:
    p = _payload(event, SkillSplit)
    topics = {("Topic", str(part.topic_id)) for part in p.into}
    return Need(nodes=frozenset({("Skill", str(p.skill_id)), *topics}))


def _skill_split(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """New skills appear; the old evidence stays on the old skill and the dispatcher re-extracts
    its episodes (Т-§2.8). A part takes the domain, status and origin of the split skill; its
    summary is not known yet — `''` (D-A-131)."""
    p = _payload(event, SkillSplit)
    old = view.node("Skill", str(p.skill_id))
    if old is None:
        return []
    changes: list[Mutation] = []
    for part in p.into:
        topic = view.resolve("Topic", str(part.topic_id))
        if topic is None:
            continue
        skill = str(part.skill_id)
        changes += [
            MergeNode(
                "Skill",
                skill,
                {
                    "learnerId": str(event.learner_id),
                    "domainId": old["domainId"],
                    "name": part.name,
                    "summary": "",
                    "status": old["status"],
                    "origin": old["origin"],
                },
            ),
            MergeRel("Topic", topic, "COVERS", "Skill", skill, {}),
        ]
    return changes


# --- tasks (Т-§4.4, Т-§5.1, Т-§5.2, Т-§5.6) --------------------------------------------------

_CHECKS = {"task.verified": "checked", "task.key_disputed": "disputed",
           "task.markup_disputed": "disputed"}  # fmt: skip


def _norm(name: str) -> str:
    """Normalized name (Т-§4.4): case and runs of spaces do not count."""
    return " ".join(name.casefold().split())


# The part of a task without parts (D-B-121).
MAIN_PART = "main"


def _all_tests(markup: TaskMarkupRecord) -> list[tuple[str, TestedLevelRecord]]:
    """Pairs of the markup with their part: `main` and the parts by their own markup."""
    return [(MAIN_PART, t) for t in markup.tests] + [
        (part.part, t) for part in markup.parts or [] for t in part.tests or []
    ]


def _markup_need(markup: TaskMarkupRecord, domain: UUID | None) -> Need:
    tests = [t for _, t in _all_tests(markup)]
    nodes = {("Skill", str(t.skill_id)) for t in tests if t.skill_id is not None}
    nodes |= {("Misconception", str(o.misconception_id)) for o in markup.options or []
              if o.misconception_id is not None}  # fmt: skip
    labels: set[tuple[str, str]] = set()
    if domain is not None:
        nodes.add(("Domain", str(domain)))
        if any(t.new_skill is not None for t in tests):
            labels = {(str(domain), "Skill"), (str(domain), "Topic")}
    return Need(nodes=frozenset(nodes), domain_labels=frozenset(labels))


def _task_needs(event: StoredEvent) -> Need:
    if event.type == "task.created":
        p = _payload(event, TaskCreated)
        return _markup_need(p.markup, event.domain_id)
    check = _payload(event, _CHECK_MODELS[event.type])
    need = Need(neighbourhoods=frozenset({("Task", str(check.task_id))}))
    if check.corrected_markup is not None:
        need = need | _markup_need(check.corrected_markup, event.domain_id)
    return need


class _Markup:
    """Changes of one markup of a task: provisional skills and topics, `TESTS {level}`, options
    and traps. New ids — UUIDv5 of `(learner_id, seq, key)` with a key per new node."""

    def __init__(self, event: StoredEvent, view: SliceView, task: str, domain: str) -> None:
        self.event, self.view, self.task, self.domain = event, view, task, domain
        self.learner = str(event.learner_id)
        self.changes: list[Mutation] = []
        self._skills: dict[str, str] | None = None
        self._topics: dict[str, str] = {}

    def _new_id(self, key: str) -> str:
        return deterministic_id(self.event.learner_id, self.event.seq, key)

    def skill_for(self, description: NewSkill) -> str | None:
        """One provisional skill per normalized name in the domain; a hint without a topic of
        that name makes a temporary topic (Т-§4.4)."""
        if self.event.domain_id is None:
            return None
        if self._skills is None:
            self._skills = {_norm(str(n.props["name"])): n.id
                            for n in self.view.domain_nodes(self.domain, "Skill")}  # fmt: skip
            self._topics = {_norm(str(n.props["name"])): n.id
                            for n in self.view.domain_nodes(self.domain, "Topic")}  # fmt: skip
        key = _norm(description.name)
        if key in self._skills:
            return self._skills[key]
        topic_key = _norm(description.topic_hint)
        topic = self._topics.get(topic_key)
        if topic is None:
            topic = self._new_id(f"topic:{topic_key}")
            self.changes += [
                MergeNode(
                    "Topic",
                    topic,
                    {
                        "learnerId": self.learner,
                        "domainId": self.domain,
                        "name": description.topic_hint.strip(),
                        "origin": "session",
                        "status": "proposed",
                        "knownClaimedAtMs": 0,
                    },
                ),
                MergeRel("Domain", self.domain, "HAS_TOPIC", "Topic", topic, {}),
            ]
            self._topics[topic_key] = topic
        skill = self._new_id(f"skill:{key}")
        self.changes += [
            MergeNode(
                "Skill",
                skill,
                {
                    "learnerId": self.learner,
                    "domainId": self.domain,
                    "name": description.name.strip(),
                    "summary": "",
                    "status": "provisional",
                    "origin": "session",
                },
            ),
            MergeRel("Topic", topic, "COVERS", "Skill", skill, {}),
        ]
        self._skills[key] = skill
        return skill

    def apply(self, markup: TaskMarkupRecord, *, replace: bool) -> list[Mutation]:
        pairs: list[tuple[str, int, str]] = []
        for part, t in _all_tests(markup):
            if t.skill_id is not None:
                skill = self.view.resolve("Skill", str(t.skill_id))
            elif t.new_skill is not None:
                skill = self.skill_for(t.new_skill)
            else:
                skill = None
            if skill is not None and (skill, t.level, part) not in pairs:
                pairs.append((skill, t.level, part))
        if replace:
            for r in self.view.out_rels("Task", self.task, "TESTS"):
                level, part = r.props["level"], r.props["part"]
                if (r.to_id, level, part) not in pairs:
                    key: Props = {"level": level, "part": part}
                    self.changes.append(
                        DeleteRel("Task", self.task, "TESTS", "Skill", r.to_id, key)
                    )
            options = self.view.out_rels("Task", self.task, "HAS_OPTION")
            self.changes += [DeleteNode("Option", r.to_id) for r in options]
        self.changes += [
            MergeRel("Task", self.task, "TESTS", "Skill", skill, {"level": level, "part": part})
            for skill, level, part in pairs
        ]
        self._options(markup, pairs[0][0] if pairs else None)
        return self.changes

    def _options(self, markup: TaskMarkupRecord, first_skill: str | None) -> None:
        """Options of a choice answer and their traps (Т-§5.2). A trap on a hypothesis is a
        misconception with status `unknown` about the first tested skill (WP3.2, D-A-132)."""
        traps = {o.option_id: o for o in markup.options or []}
        for option in markup.answer.options or []:
            trap = traps.get(option.option_id)
            node = self._new_id(f"option:{option.option_id}")
            self.changes += [
                MergeNode(
                    "Option",
                    node,
                    {
                        "learnerId": self.learner,
                        "label": option.option_id,
                        "isCorrect": option.correct,
                        "neighborIdea": trap.neighbor_idea if trap is not None else False,
                    },
                ),
                MergeRel("Task", self.task, "HAS_OPTION", "Option", node, {}),
            ]
            if trap is None:
                continue
            if trap.misconception_id is not None:
                misconception = self.view.resolve("Misconception", str(trap.misconception_id))
                if misconception is not None:
                    self.changes.append(
                        MergeRel("Option", node, "TRAPS", "Misconception", misconception, {})
                    )
            elif trap.hypothesis is not None and first_skill is not None:
                misconception = self._new_id(f"misconception:{option.option_id}")
                self.changes += [
                    MergeNode(
                        "Misconception",
                        misconception,
                        {
                            "learnerId": self.learner,
                            "domainId": self.domain,
                            "statement": trap.hypothesis,
                            "status": "unknown",
                            "triggers": [],
                            "targetedInSessionId": "",
                            "searchable": bool(trap.hypothesis.strip()),
                        },
                    ),
                    MergeRel("Misconception", misconception, "ABOUT", "Skill", first_skill, {}),
                    MergeRel("Option", node, "TRAPS", "Misconception", misconception, {}),
                ]


def _task_created(event: StoredEvent, view: SliceView) -> list[Mutation]:
    p = _payload(event, TaskCreated)
    task, domain = str(p.task_id), _id(event.domain_id)
    head: list[Mutation] = [
        MergeNode(
            "Task",
            task,
            {
                "learnerId": str(event.learner_id),
                "domainId": domain,
                "familyId": str(p.family_id),
                "source": p.source,
                "verification": p.verification,
                "methodNamed": p.markup.method_named,
            },
        ),
    ]
    return head + _Markup(event, view, task, domain).apply(p.markup, replace=False)


def _task_checked(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§5.6: the result of the check of a task; a corrected markup replaces the old one.
    Lowering the verdicts of a disputed task to class model — with the evidence (D-A-132)."""
    p = _payload(event, _CHECK_MODELS[event.type])
    task = str(p.task_id)
    props = view.node("Task", task)
    if props is None:
        return []
    changes: list[Mutation] = [SetProps("Task", task, {"verification": _CHECKS[event.type]})]
    if _CHECKS[event.type] == "disputed":
        # The verdicts already given (and what is derived from them) go down to class model:
        # T1 of code becomes T2 of a model, the same as a verdict recorded after the dispute.
        for r in view.in_rels("Task", task, "ON_TASK"):
            evidence = view.node("Evidence", r.from_id)
            if evidence is not None and evidence.get("verification") == "code":
                changes.append(
                    SetProps("Evidence", r.from_id, {"verification": "model", "tier": 2})
                )
    if p.corrected_markup is not None:
        markup = p.corrected_markup
        changes.append(SetProps("Task", task, {"methodNamed": markup.method_named}))
        changes += _Markup(event, view, task, str(props["domainId"])).apply(markup, replace=True)
    return changes


_CHECK_MODELS = {"task.verified": TaskVerified, "task.key_disputed": TaskKeyDisputed,
                 "task.markup_disputed": TaskMarkupDisputed}  # fmt: skip


# --- evidence from verdicts, cards, typos, disputes (Т-§7.2, Т-§7.8, Т-§9.1, Т-§2.8) -----------


def _evidence(
    event: StoredEvent,
    n: int,
    *,
    source: StoredEvent,
    domain: str,
    level: int,
    outcome: str,
    tier: int,
    verification: str,
    assistance: str = "none",
    hint_step: int = 0,
    gap_type: str | None = None,
    quote: str = "",
    observation_seq: int = 0,
    extractor: str = "",
    episode_seq: int = 0,
    at_ms: int | None = None,
    source_seq: int | None = None,
) -> tuple[str, Props]:
    """A new Evidence (`Т-§3.4.2`) with every property explicit (law 8). `source` — the event of
    the learner's action: its time is `atMs`, its `seq` is `sourceSeq` (law 6). Weight, novelty
    and `delayed` belong to the skill fold (phase B, `Т-§7.8` п.5): 0 / false until it runs.
    `confidence` — the learner's self-assessment 1–4 of the answer, 0 — none (D-A-134)."""
    confidence = source.payload.get("confidence") if source.type == "answer.submitted" else None
    props: Props = {
        "learnerId": str(event.learner_id),
        "domainId": domain,
        "atMs": at_ms if at_ms is not None else _ms(source.occurred_at),
        "level": level,
        "outcome": outcome,
        "assistance": assistance,
        "hintStep": hint_step,
        "novelty": 0.0,
        "confidence": confidence if isinstance(confidence, int) else 0,
        "tier": tier,
        "weight": 0.0,
        "verification": verification,
        "delayed": False,
        "solveTimeMs": 0,
        "gapType": gap_type or ("none" if outcome == "correct" else "unknown"),
        "quote": quote,
        "sourceSessionId": _id(source.session_id),
        "sessionDomainId": _id(source.domain_id),
        "sourceSeq": source_seq if source_seq is not None else source.seq,
        "derivedKind": "direct",
        "status": "active",
        "observationSeq": observation_seq,
        "extractor": extractor,
        "episodeSeq": episode_seq,
        "searchable": bool(quote.strip()),
    }
    return deterministic_id(event.learner_id, event.seq, f"evidence:{n}"), props


def _pairs(view: SliceView, task: str, part: str) -> list[tuple[str, int]]:
    """`(skill, level)` of the markup of one part of a task (`Т-§7.8` п.1)."""
    found: set[tuple[str, int]] = set()
    for r in view.out_rels("Task", task, "TESTS"):
        level = r.props["level"]
        if r.props.get("part") == part and isinstance(level, int):
            found.add((r.to_id, level))
    return sorted(found)


def _answer_needs(seq: int) -> Need:
    return Need(events=frozenset({seq}), event_refs=frozenset({(seq, "task_id", "Task")}))


def _verdict_needs(event: StoredEvent) -> Need:
    p = _payload(event, Verdict)
    need = _answer_needs(p.answer_seq)
    if p.misconception_id is not None:
        need = need | _nodes(("Misconception", str(p.misconception_id)))
    return need


def _trapped(view: SliceView, task: str, task_props: Props) -> list[str]:
    """Misconceptions a task works on (`Т-§9.1`): caught by one of its options, or the one the
    teacher's turn targeted with it (`targetsMisconceptionId`, from `turn.committed`)."""
    found = {t.to_id for o in view.out_rels("Task", task, "HAS_OPTION")
             for t in view.out_rels("Option", o.to_id, "TRAPS")}  # fmt: skip
    targeted = task_props.get("targetsMisconceptionId")
    if isinstance(targeted, str) and targeted:
        found.add(targeted)
    return sorted(found)


def _verdict_recorded(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§7.8 п.1–3, п.7; Т-§9.1. Evidence per pair of the part's markup with the verdict's
    outcome: code — T1; model — T2, or T3 when the second opinion disagreed; a disputed task
    lowers its verdicts to class model (Т-§5.6). A known misconception of the verdict (a wrong
    trap option) or its hypothesis — a manifestation; a success passing the gate (T1/T2, no
    shown solution) on a task working on a misconception — a resistance. One `MANIFESTS` /
    `RESISTED` per verdict, from its first evidence (D-A-134); `unclear` — nothing."""
    p = _payload(event, Verdict)
    answer = view.event(p.answer_seq)
    if p.outcome == "unclear" or answer is None:
        return []
    task = str(answer.payload["task_id"])
    task_props = view.node("Task", task)
    pairs = _pairs(view, task, p.part) if task_props is not None else []
    if task_props is None or not pairs:
        return []
    verification = "model" if task_props["verification"] == "disputed" else p.verification
    disagreed = p.second_opinion is not None and p.second_opinion.agreed is False
    tier = 1 if verification == "code" else 3 if disagreed else 2
    assistance = "shown_solution" if p.solution_shown else "hint" if p.help_step > 0 else "none"
    domain = str(task_props["domainId"])
    changes: list[Mutation] = []
    made: list[tuple[str, Props]] = []
    for n, (skill, level) in enumerate(pairs):
        evidence, props = _evidence(
            event,
            n,
            source=answer,
            domain=domain,
            level=level,
            outcome=p.outcome,
            tier=tier,
            verification=verification,
            assistance=assistance,
            hint_step=p.help_step,
        )
        made.append((evidence, props))
        changes += [
            MergeNode("Evidence", evidence, props),
            MergeRel("Evidence", evidence, "ON", "Skill", skill, {}),
            MergeRel("Evidence", evidence, "ON_TASK", "Task", task, {}),
        ]
    if p.outcome == "correct":
        done: set[tuple[str, str, int]] = set()
        for (evidence, props), (skill, level) in zip(made, pairs, strict=True):
            changes += _derived(event, view, (evidence, props), skill, level, task, done)
    first = deterministic_id(event.learner_id, event.seq, "evidence:0")
    if p.outcome != "correct":
        misconception = None
        if p.misconception_id is not None:
            misconception = view.resolve("Misconception", str(p.misconception_id))
        elif p.misconception_hypothesis:
            misconception = deterministic_id(event.learner_id, event.seq, "misconception")
            changes += _hypothesis(event, misconception, domain, p.misconception_hypothesis,
                                   pairs[0][0])  # fmt: skip
        if misconception is not None:
            changes.append(MergeRel("Evidence", first, "MANIFESTS", "Misconception",
                                    misconception, {}))  # fmt: skip
    elif tier <= 2 and not p.solution_shown:
        changes += [MergeRel("Evidence", first, "RESISTED", "Misconception", m, {})
                    for m in _trapped(view, task, task_props)]  # fmt: skip
    return changes


def _derived(
    event: StoredEvent,
    view: SliceView,
    source: tuple[str, Props],
    skill: str,
    level: int,
    task: str,
    done: set[tuple[str, str, int]],
) -> list[Mutation]:
    """WP4.6, `Т-§7.9`: from a direct positive evidence, one step — `indirect` to a prerequisite
    over `REQUIRES` (confidence ≥ `requires_min_confidence`), `transfer` over `SAME_IDEA` (either
    direction, ≥ `same_idea_min_confidence`); level `min(3, source level)`; the weight factor is
    the fold's (×0.5 / ×0.6). Created with the source over the relations in the slice now: a
    relation added later derives nothing from the past ([РЕШЕНИЕ Ф4])."""
    params = view.params
    targets: list[tuple[str, str]] = [
        ("indirect", r.to_id)
        for r in view.out_rels("Skill", skill, "REQUIRES")
        if _num(r.props.get("confidence")) >= params.requires_min_confidence
    ]
    for r in [
        *view.out_rels("Skill", skill, "SAME_IDEA"),
        *view.in_rels("Skill", skill, "SAME_IDEA"),
    ]:
        if _num(r.props.get("confidence")) >= params.same_idea_min_confidence:
            targets.append(("transfer", r.to_id if r.from_id == skill else r.from_id))
    source_id, source_props = source
    changes: list[Mutation] = []
    for kind, target in sorted(targets):
        key = (kind, target, min(3, level))  # one per answer: two sources must not count twice
        if target == skill or key in done or view.node("Skill", target) is None:
            continue
        done.add(key)
        node = _derived_id(event, source_id, kind, target)
        props = source_props | {"level": min(3, level), "derivedKind": kind, "quote": "",
                                "searchable": False}  # fmt: skip
        changes += [
            MergeNode("Evidence", node, props),
            MergeRel("Evidence", node, "ON", "Skill", target, {}),
            MergeRel("Evidence", node, "ON_TASK", "Task", task, {}),
            MergeRel("Evidence", node, "DERIVED_FROM", "Evidence", source_id, {}),
        ]
    return changes


def _num(value: object) -> float:
    return float(value) if isinstance(value, int | float) else 0.0


def _hypothesis(
    event: StoredEvent, misconception: str, domain: str, statement: str, skill: str
) -> list[Mutation]:
    """A misconception named in words by a model (`Т-§9.1`): `status = unknown` until its fold
    (WP5) counts the manifestations; `ABOUT` the skill of the first evidence (D-A-134)."""
    return [
        MergeNode(
            "Misconception",
            misconception,
            {
                "learnerId": str(event.learner_id),
                "domainId": domain,
                "statement": statement,
                "status": "unknown",
                "triggers": [],
                "targetedInSessionId": "",
                "searchable": bool(statement.strip()),
            },
        ),
        MergeRel("Misconception", misconception, "ABOUT", "Skill", skill, {}),
    ]


def _answer_submitted(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """D-A-125 п.1 (`Т-§14.3`, `Т-§17.5`): a card with a self-assessment — T4 evidence of level
    1 on each skill of the card's markup, outcome by the self-assessment. Other answers change
    nothing: their evidence comes from `verdict.recorded`."""
    p = _payload(event, AnswerSubmitted)
    if p.source != "card" or p.self_assessment is None:
        return []
    task = str(p.task_id)
    task_props = view.node("Task", task)
    if task_props is None:
        return []
    outcome = "correct" if p.self_assessment else "incorrect"
    skills = sorted({skill for skill, _ in _pairs(view, task, p.part or MAIN_PART)})
    changes: list[Mutation] = []
    for n, skill in enumerate(skills):
        evidence, props = _evidence(
            event,
            n,
            source=event,
            domain=str(task_props["domainId"]),
            level=1,
            outcome=outcome,
            tier=4,
            verification="model",
        )
        changes += [
            MergeNode("Evidence", evidence, props),
            MergeRel("Evidence", evidence, "ON", "Skill", skill, {}),
            MergeRel("Evidence", evidence, "ON_TASK", "Task", task, {}),
        ]
    return changes


def _disputed(view: SliceView, evidence: str, extra: Props | None = None) -> list[Mutation]:
    """The evidence and what is derived from it (`DERIVED_FROM`) leave the fold (`Т-§2.8`)."""
    props: Props = {"status": "disputed", "searchable": False} | (extra or {})
    derived = [r.from_id for r in view.in_rels("Evidence", evidence, "DERIVED_FROM")]
    return [SetProps("Evidence", e, props) for e in (evidence, *derived)]


def _verdict_evidence(view: SliceView, answer_seq: int, part: str) -> list[str]:
    """The evidence of the verdict of `(answer_seq, part)`: on the task of the answer, from that
    answer, not from an observation, on a pair of that part."""
    answer = view.event(answer_seq)
    if answer is None:
        return []
    task = str(answer.payload["task_id"])
    if view.node("Task", task) is None:
        return []
    pairs = set(_pairs(view, task, part))
    found: list[str] = []
    for r in view.in_rels("Task", task, "ON_TASK"):
        props = view.node("Evidence", r.from_id)
        if props is None or props["sourceSeq"] != answer_seq or props["observationSeq"] != 0:
            continue
        skills = [e.to_id for e in view.out_rels("Evidence", r.from_id, "ON")]
        if any((skill, props["level"]) in pairs for skill in skills):
            found.append(r.from_id)
    return found


def _typo_declared(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§2.8, `Т-§7.6`: «это была описка» — the verdict leaves the fold with what is derived
    from it; its gap type is `slip`."""
    p = _payload(event, TypoDeclared)
    changes: list[Mutation] = []
    for evidence in _verdict_evidence(view, p.answer_seq, p.part):
        changes += _disputed(view, evidence, {"gapType": "slip"})
    return changes


def _dispute_needs(event: StoredEvent) -> Need:
    p = _payload(event, DisputeFiled)
    if p.target_kind == "verdict":
        return _answer_needs(int(p.target_id.split(":", 1)[0]))
    if p.target_kind == "evidence":
        return Need(neighbourhoods=frozenset({("Evidence", p.target_id)}))
    return _nodes(("Misconception", p.target_id))


def _dispute_filed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§2.8 «не согласен»: the target is `disputed` and the fold skips it. A verdict is named
    like its key — `"<answer_seq>:<part>"` (D-A-134)."""
    p = _payload(event, DisputeFiled)
    if p.target_kind == "verdict":
        answer_seq, _, part = p.target_id.partition(":")
        changes: list[Mutation] = []
        for evidence in _verdict_evidence(view, int(answer_seq), part):
            changes += _disputed(view, evidence)
        return changes
    if p.target_kind == "evidence":
        return [] if view.node("Evidence", p.target_id) is None else _disputed(view, p.target_id)
    misconception = view.resolve("Misconception", p.target_id)
    if misconception is None:
        return []
    return [SetProps("Misconception", misconception, {"status": "disputed", "searchable": False})]


# --- the teacher's turn, closed tasks, observations (Т-§7.8, Т-§9.1, Т-§8.4, Т-§2.8) --------

# `Т-§2.8`, WP3.6: one extractor version on the hackathon (`Т-§22.10`).
EXTRACTOR_PRECEDENCE = ("extractor@3",)


def _turn_needs(event: StoredEvent) -> Need:
    p = _payload(event, TurnCommitted)
    nodes = {("Skill", str(x.skill_id)) for x in p.markup.exposures}
    nodes |= {("Task", str(t.task_id)) for t in p.markup.tasks}
    if p.markup.targets_misconception_id is not None:
        nodes.add(("Misconception", str(p.markup.targets_misconception_id)))
    return Need(nodes=frozenset(nodes))


def _turn_committed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§7.8 п.6: an `Exposure` per exposure of the markup, `OF` its skill. Т-§9.1: a turn that
    targets a misconception marks it with the session (`targetedInSessionId`) and marks its
    tasks (`targetsMisconceptionId`, read for `RESISTED`). Temporary skills of the markup come
    with `task.created` of the same append (D-A-180)."""
    p = _payload(event, TurnCommitted)
    learner = str(event.learner_id)
    changes: list[Mutation] = []
    for n, exposure in enumerate(p.markup.exposures):
        skill = view.resolve("Skill", str(exposure.skill_id))
        if skill is None:
            continue
        node = deterministic_id(event.learner_id, event.seq, f"exposure:{n}")
        props: Props = {
            "learnerId": learner,
            "atMs": _ms(event.occurred_at),
            "kind": exposure.kind,
            "approach": exposure.approach,
            "sourceSeq": event.seq,
            "sessionDomainId": _id(event.domain_id),
        }
        changes += [
            MergeNode("Exposure", node, props),
            MergeRel("Exposure", node, "OF", "Skill", skill, {}),
        ]
    target = p.markup.targets_misconception_id
    misconception = view.resolve("Misconception", str(target)) if target is not None else None
    if misconception is None:
        return changes
    if event.session_id is not None:
        session: Props = {"targetedInSessionId": str(event.session_id)}
        changes.append(SetProps("Misconception", misconception, session))
    for task in p.markup.tasks:
        live = view.resolve("Task", str(task.task_id))
        if live is not None:
            changes.append(SetProps("Task", live, {"targetsMisconceptionId": misconception}))
    return changes


def _task_closed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§8.4: a solution shown on request needs consolidation; the skill fold (WP4.5) reads how
    the tasks of the skill were closed (D-A-180)."""
    p = _payload(event, TaskClosed)
    task = view.resolve("Task", str(p.task_id))
    if task is None:
        return []
    return [SetProps("Task", task, {"closedHow": p.how, "closedAtMs": _ms(event.occurred_at)})]


def _superseded(view: SliceView, evidence: str) -> list[Mutation]:
    props: Props = {"status": "superseded", "searchable": False}
    derived = [r.from_id for r in view.in_rels("Evidence", evidence, "DERIVED_FROM")]
    return [SetProps("Evidence", e, props) for e in (evidence, *derived)]


def _observation_needs(event: StoredEvent) -> Need:
    p = _payload(event, ObservationExtracted)
    nodes: set[tuple[str, str]] = set()
    for el in p.elements:
        if el.skill_id is not None:
            nodes.add(("Skill", str(el.skill_id)))
        if el.misconception_id is not None:
            nodes.add(("Misconception", str(el.misconception_id)))
        if el.root_cause is not None:
            nodes.add(("Skill", str(el.root_cause.skill_id)))
    labels: frozenset[tuple[str, str]] = frozenset()
    if event.domain_id is not None and any(el.new_skill is not None for el in p.elements):
        nodes.add(("Domain", str(event.domain_id)))
        labels = frozenset({(str(event.domain_id), "Skill"), (str(event.domain_id), "Topic")})
    return Need(
        nodes=frozenset(nodes),
        domain_labels=labels,
        matches=frozenset({("Evidence", "episodeSeq", p.episode_seq)}),
    )


def _observation_extracted(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """Т-§2.8: the observation of the most preferred extractor, the latest of its seq, is the
    current one of the episode; the earlier evidence of the episode becomes `superseded`.
    Т-§7.8 п.4: elements — T3 evidence with their quote, help, gap type and the time and `seq`
    of the latest source event; «неясно» — none; a new skill — provisional (`Т-§4.4`); a known
    misconception or a hypothesis — a manifestation (`Т-§9.1`); a root cause — `ROOT_CAUSE`
    with the confidence of its category (`root_cause_confidence_map`, `Т-§9.4`). D-A-180."""
    p = _payload(event, ObservationExtracted)
    if p.extractor not in EXTRACTOR_PRECEDENCE:
        return []
    changes: list[Mutation] = []
    for earlier in view.matching("Evidence", "episodeSeq", p.episode_seq):
        observed = earlier.props.get("observationSeq")
        if observed not in (0, event.seq) and earlier.props.get("status") == "active":
            changes += _superseded(view, earlier.id)
    domain = _id(event.domain_id)
    markup = _Markup(event, view, "", domain)
    for n, el in enumerate(p.elements):
        if el.skill_id is not None:
            skill = view.resolve("Skill", str(el.skill_id))
        elif el.new_skill is not None:
            skill = markup.skill_for(el.new_skill)
            changes += markup.changes
            markup.changes = []
        else:
            skill = None
        if skill is None:
            continue
        evidence, props = _evidence(
            event,
            n,
            source=event,
            domain=domain,
            level=el.level or 0,
            outcome=el.outcome or "incorrect",
            tier=3,
            verification="model",
            assistance=el.help,
            hint_step=el.hint_step or 0,
            gap_type=el.gap_type,
            quote=el.quote,
            observation_seq=event.seq,
            extractor=p.extractor,
            episode_seq=p.episode_seq,
            at_ms=el.at_ms,
            source_seq=max(el.events, default=event.seq),
        )
        changes += [
            MergeNode("Evidence", evidence, props),
            MergeRel("Evidence", evidence, "ON", "Skill", skill, {}),
        ]
        misconception = None
        if el.misconception_id is not None:
            misconception = view.resolve("Misconception", str(el.misconception_id))
        elif el.new_misconception:
            misconception = deterministic_id(event.learner_id, event.seq, f"misconception:{n}")
            changes += _hypothesis(event, misconception, domain, el.new_misconception, skill)
        if misconception is not None:
            changes.append(
                MergeRel("Evidence", evidence, "MANIFESTS", "Misconception", misconception, {})
            )
        cause = view.resolve("Skill", str(el.root_cause.skill_id)) if el.root_cause else None
        if el.root_cause is not None and cause is not None:
            weight = view.params.root_cause_confidence[el.root_cause.confidence]
            changes.append(
                MergeRel("Evidence", evidence, "ROOT_CAUSE", "Skill", cause, {"confidence": weight})
            )
    return changes


def _diagnosed_needs(event: StoredEvent) -> Need:
    p = _payload(event, ObservationDiagnosed)
    near: set[tuple[str, str]] = set()
    nodes: set[tuple[str, str]] = set()
    if p.diagnosis.misconception_id is not None:
        near.add(("Misconception", str(p.diagnosis.misconception_id)))
    if p.task_id is not None:
        near.add(("Task", str(p.task_id)))
    if p.diagnosis.root_cause_skill_id is not None:
        nodes.add(("Skill", str(p.diagnosis.root_cause_skill_id)))
    return Need(nodes=frozenset(nodes), neighbourhoods=frozenset(near))


def _diagnosis_skills(view: SliceView, p: ObservationDiagnosed) -> tuple[str | None, list[str]]:
    """The named misconception and its main skill; else the skills the task tests."""
    misconception = None
    if p.diagnosis.misconception_id is not None:
        ref = str(p.diagnosis.misconception_id)
        misconception = view.resolve("Misconception", ref)
        about = [r.to_id for r in view.out_rels("Misconception", ref, "ABOUT")]
        if misconception is not None and about:
            return misconception, about[:1]
    if p.task_id is not None and view.node("Task", str(p.task_id)) is not None:
        return misconception, sorted(
            {r.to_id for r in view.out_rels("Task", str(p.task_id), "TESTS")}
        )
    return misconception, []


def _observation_diagnosed(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """D-A-125 п.2 (`Т-§15.4`, `Т-§7.2`): a found error of a canvas line — evidence of level 0,
    outcome incorrect, T3, on the skill of the named misconception, else on the skills the task
    tests; neither — nothing (law 8). The misconception or the hypothesis — a manifestation;
    the root cause — `ROOT_CAUSE` with the diagnosis confidence."""
    p = _payload(event, ObservationDiagnosed)
    misconception, skills = _diagnosis_skills(view, p)
    if not skills:
        return []
    task = view.resolve("Task", str(p.task_id)) if p.task_id is not None else None
    domain = _id(event.domain_id)
    changes: list[Mutation] = []
    for n, skill in enumerate(skills):
        evidence, props = _evidence(
            event,
            n,
            source=event,
            domain=domain,
            level=0,
            outcome="incorrect",
            tier=3,
            verification="model",
            gap_type=p.diagnosis.gap_type,
            observation_seq=event.seq,
        )
        changes += [
            MergeNode("Evidence", evidence, props),
            MergeRel("Evidence", evidence, "ON", "Skill", skill, {}),
        ]
        if task is not None:
            changes.append(MergeRel("Evidence", evidence, "ON_TASK", "Task", task, {}))
    first = deterministic_id(event.learner_id, event.seq, "evidence:0")
    hypothesis = p.diagnosis.misconception_hypothesis
    if misconception is None and hypothesis:
        misconception = deterministic_id(event.learner_id, event.seq, "misconception")
        changes += _hypothesis(event, misconception, domain, hypothesis, skills[0])
    if misconception is not None:
        changes.append(MergeRel("Evidence", first, "MANIFESTS", "Misconception", misconception, {}))
    root = p.diagnosis.root_cause_skill_id
    cause = view.resolve("Skill", str(root)) if root is not None else None
    if cause is not None:
        changes.append(
            MergeRel("Evidence", first, "ROOT_CAUSE", "Skill", cause, {"confidence": p.confidence})
        )
    return changes


def _second_opinion_needs(event: StoredEvent) -> Need:
    target = int(event.payload["target_seq"])
    return Need(matches=frozenset({("Evidence", "observationSeq", target)}))


def _second_opinion(event: StoredEvent, view: SliceView) -> list[Mutation]:
    """D-A-125 п.3 (`Т-§7.7`): an agreed second opinion raises the diagnosis evidence T3 -> T2;
    a disagreed one leaves it T3."""
    p = _payload(event, ObservationSecondOpinion)
    if not p.agreed:
        return []
    return [
        SetProps("Evidence", e.id, {"tier": 2})
        for e in view.matching("Evidence", "observationSeq", p.target_seq)
        if e.props.get("episodeSeq") == 0 and e.props.get("tier") == 3
    ]


# Event type -> handler; exactly the types of `ROWS` (T-P4).
HANDLERS: dict[str, Handler] = {
    "domain.created": Handler(_none, _domain_created),
    "topic.proposed": Handler(_topic_new_needs, _topic_new),
    "topic.created": Handler(_topic_new_needs, _topic_new),
    "topic.renamed": Handler(_ref("Topic", "topic_id"), _topic_set),
    "topic.hidden": Handler(_ref("Topic", "topic_id"), _topic_set),
    "skill.created": Handler(_ref("Topic", "topic_id"), _skill_created),
    "edge.added": Handler(_edge_needs, _edge),
    "edge.removed": Handler(_edge_needs, _edge),
    "hub.created": Handler(_ref("Domain", "domain_id"), _hub_created),
    "hub.deadline_set": Handler(_ref("Hub", "hub_id"), _hub_deadline_set),
    "hub.topics_changed": Handler(_hub_topics_needs, _hub_topics_changed),
    "embedding.ready": Handler(_embedding_needs, _embedding_ready),
    "known.claimed": Handler(_ref("Topic", "topic_id"), _known_claimed),
    "preference.set": Handler(lambda e: _nodes(("Profile", _profile_id(e))), _preference_set),
    "material.uploaded": Handler(_none, _material_uploaded),
    "material.parsed": Handler(_ref("Material", "material_id"), _material_parsed),
    "chunk.indexed": Handler(_chunk_needs, _chunk_indexed),
    "digest.written": Handler(_ref("Domain", "domain_id"), _digest_written),
    "misconception.described": Handler(
        _ref("Misconception", "misconception_id"), _misconception_described
    ),
    "session.ended": Handler(_session_ended_needs, _session_ended),
    "skill.merged": Handler(_merge_needs, _merged),
    "topic.merged": Handler(_merge_needs, _merged),
    "misconception.merged": Handler(_merge_needs, _merged),
    "topic.split": Handler(_topic_split_needs, _topic_split),
    "skill.split": Handler(_skill_split_needs, _skill_split),
    "task.created": Handler(_task_needs, _task_created),
    "task.verified": Handler(_task_needs, _task_checked),
    "task.key_disputed": Handler(_task_needs, _task_checked),
    "task.markup_disputed": Handler(_task_needs, _task_checked),
    "verdict.recorded": Handler(_verdict_needs, _verdict_recorded),
    "answer.submitted": Handler(
        lambda e: Need(neighbourhoods=frozenset({("Task", str(e.payload["task_id"]))})),
        _answer_submitted,
    ),
    "typo.declared": Handler(lambda e: _answer_needs(int(e.payload["answer_seq"])), _typo_declared),
    "dispute.filed": Handler(_dispute_needs, _dispute_filed),
    "turn.committed": Handler(_turn_needs, _turn_committed),
    "task.closed": Handler(_ref("Task", "task_id"), _task_closed),
    "observation.extracted": Handler(_observation_needs, _observation_extracted),
    "observation.diagnosed": Handler(_diagnosed_needs, _observation_diagnosed),
    "observation.second_opinion": Handler(_second_opinion_needs, _second_opinion),
}
