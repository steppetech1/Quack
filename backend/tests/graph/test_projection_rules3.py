"""ТЗ Ф4 WP3.2 (§8.4): handlers of phase A, `rules@3` — the slice, the table, aliases.

T-P3 `test_handler_outside_slice_is_bug`, T-P4 `test_handler_table` (by groups of the S5 table),
T-P5 `test_alias_and_empty_apply`; the unknown type of T-P6 at the handler level (the stopped
stream and `ops resume` — with the batch loop, WP3.3).
"""

from __future__ import annotations

import base64
import struct
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest

import quack.events.event_types  # noqa: F401  (registers the catalog before SPECS is read)
from quack.events.log import StoredEvent
from quack.events.registry import SPECS, UnknownEventTypeError
from quack.graph.ids import deterministic_id
from quack.graph.projection.handlers import HANDLERS, handler_for
from quack.graph.projection.phase_a import apply_events
from quack.graph.projection.types import NodeRecord, Props, RelKey, RelRecord, SubGraph
from quack.graph.projection.view import BatchSlice, Need, OutsideSliceError
from quack.graph.projector_rows import ROWS
from tests.events.samples import sample_payload

LEARNER = UUID(int=0xA0)
L = str(LEARNER)
DOMAIN = str(UUID(int=0xD0))
TOPIC = str(UUID(int=0x71))
TOPIC_B = str(UUID(int=0x72))
SKILL = str(UUID(int=0x51))
SKILL_B = str(UUID(int=0x52))
SKILL_OLD = str(UUID(int=0x59))
HUB = str(UUID(int=0xB0))
AT = datetime(2026, 11, 3, tzinfo=UTC)


def _event(seq: int, event_type: str, payload: dict[str, Any], **envelope: Any) -> StoredEvent:
    return StoredEvent(
        learner_id=LEARNER,
        seq=seq,
        id=seq,
        type=event_type,
        schema_version=envelope.pop("schema_version", 1),
        actor="test",
        domain_id=envelope.pop("domain_id", None),
        session_id=envelope.pop("session_id", None),
        causation_seq=None,
        correlation_id=None,
        idempotency_key=f"key:{seq}",
        payload_hash="hash",
        occurred_at=AT,
        payload=payload,
        source_refs=[],
    )


def _node(label: str, node_id: str, **props: Any) -> NodeRecord:
    return NodeRecord(label, node_id, {"learnerId": L, **props})


def _rel(a: tuple[str, str], rel_type: str, b: tuple[str, str], **props: Any) -> RelRecord:
    return RelRecord(a[0], a[1], rel_type, b[0], b[1], dict(props))


def _graph(batch: BatchSlice) -> tuple[dict[tuple[str, str], Props], dict[RelKey, Props]]:
    shot = batch.snapshot()
    return {(n.label, n.id): n.props for n in shot.nodes}, {r.key: r.props for r in shot.rels}


def _run(
    events: Sequence[StoredEvent], nodes: Sequence[NodeRecord] = (), rels: Sequence[RelRecord] = ()
) -> tuple[dict[tuple[str, str], Props], dict[RelKey, Props]]:
    batch = BatchSlice(L, SubGraph(nodes=list(nodes), rels=list(rels)))
    apply_events(events, batch)
    return _graph(batch)


_DOMAIN = _node("Domain", DOMAIN, name="Algebra", status="active")
_TOPIC = _node("Topic", TOPIC, domainId=DOMAIN, name="Fractions", origin="session",
               status="active", knownClaimedAtMs=0)  # fmt: skip
_TOPIC_B = _node("Topic", TOPIC_B, domainId=DOMAIN, name="Ratios", origin="session",
                 status="active", knownClaimedAtMs=0)  # fmt: skip
_SKILL = _node("Skill", SKILL, domainId=DOMAIN, name="add fractions", summary="s",
               status="active", origin="session")  # fmt: skip
_SKILL_B = _node("Skill", SKILL_B, domainId=DOMAIN, name="common denominator", summary="s",
                 status="active", origin="session")  # fmt: skip
_HUB = _node("Hub", HUB, domainId=DOMAIN, name="Exam", kind="request", origin="learner",
             controlType="tasks", deadlineAtMs=0, status="active")  # fmt: skip
_ALIAS = NodeRecord("Alias", SKILL_OLD, {"learnerId": L})
_ALIAS_OF = _rel(("Alias", SKILL_OLD), "ALIAS_OF", ("Skill", SKILL))


# --- the table and the catalog (T-P4, T-P6) -------------------------------------------------


def test_handler_table() -> None:
    """T-P4 (WP3.2): the table test is this module — the groups below give each event its
    expected changes of the graph; here: one handler per row of S5, the catalog types «без
    обработчика» have none (Т-§2.4)."""
    assert set(HANDLERS) == {row.event_type for row in ROWS}
    assert len(SPECS) > len(ROWS)
    for (event_type, version), spec in SPECS.items():
        handler = handler_for(_event(1, event_type, {}, schema_version=version))
        assert (handler is not None) == spec.projector, event_type


def test_unknown_type_or_newer_schema_is_an_error() -> None:
    """T-P6 at the handler level: an error, never a skip (Т-§2.4)."""
    with pytest.raises(UnknownEventTypeError):
        handler_for(_event(1, "comet.sighted", {}))
    with pytest.raises(UnknownEventTypeError):
        handler_for(_event(1, "topic.renamed", {}, schema_version=2))


# --- the slice (T-P3) -----------------------------------------------------------------------


def test_handler_outside_slice_is_bug() -> None:
    batch = BatchSlice(L, SubGraph(nodes=[_SKILL, _SKILL_B, _TOPIC], rels=[]))
    view = batch.view(Need(nodes=frozenset({("Skill", SKILL)})))
    assert view.node("Skill", SKILL) == _SKILL.props
    with pytest.raises(OutsideSliceError):
        view.node("Skill", SKILL_B)
    with pytest.raises(OutsideSliceError):
        view.out_rels("Skill", SKILL)  # only the node is declared, not its relationships
    with pytest.raises(OutsideSliceError):
        view.domain_nodes(DOMAIN, "Topic")
    near = batch.view(Need(neighbourhoods=frozenset({("Topic", TOPIC)})))
    assert near.out_rels("Topic", TOPIC) == []
    assert batch.view(Need(domain_labels=frozenset({(DOMAIN, "Skill")}))).domain_nodes(
        DOMAIN, "Skill"
    ) == sorted([_SKILL, _SKILL_B], key=lambda n: n.id)


def test_needs_of_a_batch_join() -> None:
    a = Need(nodes=frozenset({("Skill", SKILL)}))
    b = Need(
        neighbourhoods=frozenset({("Topic", TOPIC)}), domain_labels=frozenset({(DOMAIN, "Skill")})
    )
    assert a | b == Need(
        nodes=a.nodes, neighbourhoods=b.neighbourhoods, domain_labels=b.domain_labels
    )


# --- structure (T-P4) -----------------------------------------------------------------------


def test_domain_created_makes_the_domain_and_its_whole_domain_hub() -> None:
    """Т-§4.8: the hub «весь домен» is created with the domain; its composition is not stored."""
    nodes, rels = _run([_event(1, "domain.created", {"domain_id": DOMAIN, "name": "Algebra"})])
    hub = deterministic_id(LEARNER, 1, 0)
    assert nodes[("Domain", DOMAIN)] == _DOMAIN.props
    assert nodes[("Hub", hub)] == {
        "learnerId": L,
        "domainId": DOMAIN,
        "name": "Algebra",
        "kind": "domain_all",
        "origin": "learner",
        "controlType": "no_goal",
        "deadlineAtMs": 0,
        "status": "active",
    }
    assert ("Learner", L, "OWNS", "Domain", DOMAIN) in rels
    assert ("Domain", DOMAIN, "HAS_HUB", "Hub", hub) in rels
    assert not any(key[2] == "INCLUDES" for key in rels)


@pytest.mark.parametrize(("event_type", "status"), [("topic.created", "active"),
                                                    ("topic.proposed", "proposed")])  # fmt: skip
def test_topic_created_or_proposed(event_type: str, status: str) -> None:
    payload = {"topic_id": TOPIC, "domain_id": DOMAIN, "name": "Fractions", "origin": "session"}
    nodes, rels = _run([_event(2, event_type, payload)], [_DOMAIN])
    assert nodes[("Topic", TOPIC)] == _TOPIC.props | {"status": status}
    assert rels[("Domain", DOMAIN, "HAS_TOPIC", "Topic", TOPIC)] == {}


def test_topic_renamed_and_hidden() -> None:
    nodes, _ = _run(
        [
            _event(3, "topic.renamed", {"topic_id": TOPIC, "name": "Fraction sums"}),
            _event(4, "topic.hidden", {"topic_id": TOPIC}),
        ],
        [_DOMAIN, _TOPIC],
    )
    assert nodes[("Topic", TOPIC)] == _TOPIC.props | {"name": "Fraction sums", "status": "hidden"}


def test_skill_created_is_covered_by_its_home_topic() -> None:
    payload = {"skill_id": SKILL, "topic_id": TOPIC, "domain_id": DOMAIN, "name": "add fractions",
               "summary": "s", "status": "active", "origin": "session"}  # fmt: skip
    nodes, rels = _run([_event(5, "skill.created", payload)], [_DOMAIN, _TOPIC])
    assert nodes[("Skill", SKILL)] == _SKILL.props
    assert rels[("Topic", TOPIC, "COVERS", "Skill", SKILL)] == {}


def test_edges_added_and_removed() -> None:
    requires = {"kind": "requires", "from_id": SKILL, "to_id": SKILL_B}
    _, rels = _run(
        [
            _event(6, "edge.added", requires | {"origin": "model", "confidence": 0.7}),
            _event(
                7,
                "edge.added",
                {
                    "kind": "same_idea",
                    "from_id": SKILL,
                    "to_id": SKILL_B,
                    "origin": "model",
                    "confidence": 0.8,
                },
            ),
        ],
        [_SKILL, _SKILL_B],
    )
    assert rels[("Skill", SKILL, "REQUIRES", "Skill", SKILL_B)] == {
        "origin": "model",
        "confidence": 0.7,
    }
    assert rels[("Skill", SKILL, "SAME_IDEA", "Skill", SKILL_B)] == {"confidence": 0.8}
    _, rels = _run(
        [_event(8, "edge.removed", requires)],
        [_SKILL, _SKILL_B],
        [_rel(("Skill", SKILL), "REQUIRES", ("Skill", SKILL_B), origin="model", confidence=0.7)],
    )
    assert ("Skill", SKILL, "REQUIRES", "Skill", SKILL_B) not in rels


@pytest.mark.parametrize(("stored", "removed"), [((SKILL, SKILL_B), (SKILL_B, SKILL)),
                                                 ((SKILL, SKILL_B), (SKILL, SKILL_B))])  # fmt: skip
def test_same_idea_is_removed_whichever_way_it_was_stored(
    stored: tuple[str, str], removed: tuple[str, str]
) -> None:
    """Т-§3.4.3: `SAME_IDEA` is stored one way and read without a direction — `edge.removed`
    names the pair in any order and removes the relation."""
    payload = {"kind": "same_idea", "from_id": removed[0], "to_id": removed[1]}
    _, rels = _run(
        [_event(9, "edge.removed", payload)],
        [_SKILL, _SKILL_B],
        [_rel(("Skill", stored[0]), "SAME_IDEA", ("Skill", stored[1]), confidence=0.8)],
    )
    assert not [k for k in rels if k[2] == "SAME_IDEA"]


def test_hub_created_deadline_and_topics() -> None:
    created = {"hub_id": HUB, "domain_id": DOMAIN, "name": "Exam", "kind": "request",
               "origin": "learner", "control_type": "tasks"}  # fmt: skip
    nodes, rels = _run(
        [
            _event(9, "hub.created", created),
            _event(
                10,
                "hub.deadline_set",
                {"hub_id": HUB, "deadline_at_ms": 1_800_000_000_000, "source": "learner"},
            ),
            _event(
                11,
                "hub.topics_changed",
                {
                    "hub_id": HUB,
                    "topics": [
                        {"topic_id": TOPIC, "required_levels": [3, 4]},
                        {"topic_id": TOPIC_B, "required_levels": [2]},
                    ],
                },
            ),
            _event(
                12,
                "hub.topics_changed",
                {
                    "hub_id": HUB,
                    "topics": [
                        {"topic_id": TOPIC, "required_levels": [3]},
                    ],
                },
            ),
        ],
        [_DOMAIN, _TOPIC, _TOPIC_B],
    )
    assert nodes[("Hub", HUB)] == _HUB.props | {"deadlineAtMs": 1_800_000_000_000}
    assert ("Domain", DOMAIN, "HAS_HUB", "Hub", HUB) in rels
    assert rels[("Hub", HUB, "INCLUDES", "Topic", TOPIC)] == {"requiredLevels": [3]}
    assert ("Hub", HUB, "INCLUDES", "Topic", TOPIC_B) not in rels  # the event gives the set
    nodes, _ = _run(
        [
            _event(
                13, "hub.deadline_set", {"hub_id": HUB, "deadline_at_ms": None, "source": "learner"}
            )
        ],
        [_DOMAIN, _HUB],
    )
    assert nodes[("Hub", HUB)]["deadlineAtMs"] == 0  # law 8: removed deadline — 0 «не было»


# --- aliases and the empty application (T-P5) -----------------------------------------------


def _vector(values: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def test_alias_and_empty_apply() -> None:
    """`embedding.ready` for a merged skill lands on the target; for a missing one nothing
    changes (Т-§2.4: the event is applied as empty, the watermark still moves)."""
    ready = {"node_kind": "skill", "model": "m", "dim": 2, "vector": _vector([0.5, -1.0])}
    nodes, _ = _run(
        [_event(14, "embedding.ready", ready | {"node_id": SKILL_OLD})],
        [_SKILL, _ALIAS],
        [_ALIAS_OF],
    )
    assert nodes[("Skill", SKILL)]["embedding"] == [0.5, -1.0]
    assert ("Skill", SKILL_OLD) not in nodes
    before = _run([], [_SKILL], [])
    after = _run([_event(15, "embedding.ready", ready | {"node_id": SKILL_B})], [_SKILL], [])
    assert after == before


def test_proposed_topic_created_later_keeps_what_it_had() -> None:
    """A proposed topic that the learner accepts keeps its own values (here «known»)."""
    proposed = _node("Topic", TOPIC, domainId=DOMAIN, name="Fractions", origin="material",
                     status="proposed", knownClaimedAtMs=5)  # fmt: skip
    payload = {"topic_id": TOPIC, "domain_id": DOMAIN, "name": "Fractions", "origin": "learner"}
    nodes, rels = _run([_event(2, "topic.created", payload)], [_DOMAIN, proposed])
    assert nodes[("Topic", TOPIC)] == proposed.props | {"status": "active", "origin": "learner"}
    assert ("Domain", DOMAIN, "HAS_TOPIC", "Topic", TOPIC) not in rels  # not created twice


def test_edge_between_merged_skills_is_not_a_loop() -> None:
    """After `skill.merged` both ends may resolve to one skill (Т-§2.8): no relation to itself."""
    edge = {"kind": "requires", "from_id": SKILL_OLD, "to_id": SKILL, "origin": "model",
            "confidence": 0.9}  # fmt: skip
    _, rels = _run([_event(6, "edge.added", edge)], [_SKILL, _ALIAS], [_ALIAS_OF])
    assert ("Skill", SKILL, "REQUIRES", "Skill", SKILL) not in rels


# --- learner edits, profile, materials, digest, misconceptions (T-P4, group 2) ---------------

MATERIAL = str(UUID(int=0xC2))
CHUNK = str(UUID(int=0xC1))
MISCONCEPTION = str(UUID(int=0x61))
SESSION = UUID(int=0x5E)
AT_MS = int(AT.timestamp() * 1000)
PROFILE = deterministic_id(LEARNER, 0, "profile")
_MATERIAL = _node("Material", MATERIAL, domainId=DOMAIN, mime="application/pdf",
                  sessionId=str(SESSION), pagesCount=0)  # fmt: skip
_MISCONCEPTION = _node("Misconception", MISCONCEPTION, domainId=DOMAIN, statement="old",
                       triggers=[], targetedInSessionId=str(SESSION))  # fmt: skip


def test_known_claimed_marks_the_topic() -> None:
    nodes, _ = _run([_event(20, "known.claimed", {"topic_id": TOPIC})], [_DOMAIN, _TOPIC])
    assert nodes[("Topic", TOPIC)] == _TOPIC.props | {"knownClaimedAtMs": AT_MS}


def test_preference_set_writes_the_profile() -> None:
    """Т-§9.5: explicit preferences `pref*` on the learner's only Profile (WP5.3)."""
    nodes, rels = _run(
        [
            _event(21, "preference.set", {"key": "language", "value": "ru"}),
            _event(22, "preference.set", {"key": "canvas_quiet", "value": True}),
        ]
    )
    assert nodes[("Profile", PROFILE)] == {
        "learnerId": L,
        "prefLanguage": "ru",
        "prefCanvasQuiet": True,
    }
    assert ("Learner", L, "HAS_PROFILE", "Profile", PROFILE) in rels


def test_material_uploaded_parsed_and_chunk_indexed() -> None:
    """`chunk_emb` filters by learnerId, domainId, searchable (Т-§3.4.6)."""
    upload = {"material_id": MATERIAL, "object_key": "k", "mime": "application/pdf",
              "session_id": str(SESSION)}  # fmt: skip
    chunk = {"chunk_id": CHUNK, "material_id": MATERIAL, "page": 4, "text": "a/b + c/d",
             "notation": ["a/b"], "teaches": [SKILL], "uses": [SKILL_OLD]}  # fmt: skip
    nodes, rels = _run(
        [
            _event(23, "material.uploaded", upload, domain_id=UUID(DOMAIN)),
            _event(
                24,
                "material.parsed",
                {"material_id": MATERIAL, "pages_count": 12},
                domain_id=UUID(DOMAIN),
            ),
            _event(25, "chunk.indexed", chunk, domain_id=UUID(DOMAIN)),
        ],
        [_DOMAIN, _SKILL, _ALIAS],
        [_ALIAS_OF],
    )
    assert nodes[("Material", MATERIAL)] == _MATERIAL.props | {"pagesCount": 12}
    assert nodes[("Chunk", CHUNK)] == {
        "learnerId": L,
        "domainId": DOMAIN,
        "page": 4,
        "text": "a/b + c/d",
        "notation": ["a/b"],
        "searchable": True,
    }
    assert ("Material", MATERIAL, "HAS_CHUNK", "Chunk", CHUNK) in rels
    assert ("Chunk", CHUNK, "TEACHES", "Skill", SKILL) in rels
    assert ("Chunk", CHUNK, "USES", "Skill", SKILL) in rels  # through the alias


def test_chunk_without_text_is_not_searchable() -> None:
    chunk = {"chunk_id": CHUNK, "material_id": MATERIAL, "page": 1, "text": " ",
             "notation": [], "teaches": [], "uses": []}  # fmt: skip
    nodes, _ = _run([_event(26, "chunk.indexed", chunk, domain_id=UUID(DOMAIN))], [_MATERIAL])
    assert nodes[("Chunk", CHUNK)]["searchable"] is False


_STRUCTURED = {"frontier": [], "gap_hypotheses": [], "uncertainty": None, "moves": [],
               "forecast": []}  # fmt: skip


def test_digest_written_replaces_the_digest_of_the_domain() -> None:
    """`Т-§13.5`: the latest digest of a domain; the teacher manner goes to the profile (WP5.3)."""
    digest = {"domain_id": DOMAIN, "text": "t1", "as_of_seq": 30,
              "structured": _STRUCTURED,
              "plan_divergence": False, "manner": "short answers"}  # fmt: skip
    nodes, rels = _run(
        [
            _event(31, "digest.written", digest),
            _event(41, "digest.written", digest | {"text": "t2", "as_of_seq": 40, "manner": "m2"}),
        ],
        [_DOMAIN],
    )
    digests = {key: props for key, props in nodes.items() if key[0] == "Digest"}
    assert list(digests.values()) == [
        {"learnerId": L, "domainId": DOMAIN, "text": "t2", "asOfSeq": 40}
    ]
    ((_, digest_id),) = digests
    assert ("Domain", DOMAIN, "HAS_DIGEST", "Digest", digest_id) in rels
    assert nodes[("Profile", PROFILE)]["learnedManner"] == "m2"


OTHER = str(UUID(int=0x62))
_OTHER_TARGET = _node("Misconception", OTHER, domainId=DOMAIN, statement="o", triggers=[],
                      targetedInSessionId=str(UUID(int=0x5F)))  # fmt: skip


def test_misconception_described_and_session_ended() -> None:
    described = {"misconception_id": MISCONCEPTION, "statement": "adds denominators",
                 "triggers": ["unlike denominators"]}  # fmt: skip
    nodes, _ = _run(
        [
            _event(42, "misconception.described", described),
            _event(
                43,
                "session.ended",
                {"session_id": str(SESSION), "reason": "learner"},
                domain_id=UUID(DOMAIN),
                session_id=SESSION,
            ),
        ],
        [_DOMAIN, _MISCONCEPTION, _OTHER_TARGET],
    )
    assert nodes[("Misconception", OTHER)] == _OTHER_TARGET.props  # targeted in another session
    assert nodes[("Misconception", MISCONCEPTION)] == _MISCONCEPTION.props | {
        "statement": "adds denominators",
        "triggers": ["unlike denominators"],
        "targetedInSessionId": "",  # law 8: «not targeted» is an explicit value
    }


# --- merges and splits (T-P4, group 3; Т-§2.8) ----------------------------------------------

EVIDENCE = str(UUID(int=0xE1))
OPTION = str(UUID(int=0x0B))
TOPIC_NEW = str(UUID(int=0x73))
SKILL_P1 = str(UUID(int=0x54))
SKILL_P2 = str(UUID(int=0x55))
ALIAS_EARLIER = str(UUID(int=0x5A))
_SKILL_OLD = _node("Skill", SKILL_OLD, domainId=DOMAIN, name="add fractions 2", summary="s",
                   status="provisional", origin="material")  # fmt: skip
_EVIDENCE = _node("Evidence", EVIDENCE, domainId=DOMAIN, status="active")


def test_skill_merged_moves_relations_and_leaves_an_alias() -> None:
    """Relations and evidence go to the target, the old node is deleted, its id becomes an
    alias; earlier aliases of the old node now lead to the target. The old home topic is not
    moved (a skill has exactly one, `Т-§3.4.3`); a relation to the target itself disappears."""
    nodes, rels = _run(
        [_event(50, "skill.merged", {"from_id": SKILL_OLD, "into_id": SKILL})],
        [
            _TOPIC,
            _TOPIC_B,
            _SKILL,
            _SKILL_B,
            _SKILL_OLD,
            _EVIDENCE,
            NodeRecord("Alias", ALIAS_EARLIER, {"learnerId": L}),
        ],
        [
            _rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL)),
            _rel(("Topic", TOPIC_B), "COVERS", ("Skill", SKILL_OLD)),
            _rel(
                ("Skill", SKILL_OLD), "REQUIRES", ("Skill", SKILL_B), origin="model", confidence=0.7
            ),
            _rel(
                ("Skill", SKILL), "REQUIRES", ("Skill", SKILL_OLD), origin="model", confidence=0.9
            ),
            _rel(("Skill", SKILL_OLD), "SAME_IDEA", ("Skill", SKILL), confidence=0.8),
            _rel(("Evidence", EVIDENCE), "ON", ("Skill", SKILL_OLD)),
            _rel(("Alias", ALIAS_EARLIER), "ALIAS_OF", ("Skill", SKILL_OLD)),
        ],
    )
    assert ("Skill", SKILL_OLD) not in nodes
    assert nodes[("Alias", SKILL_OLD)] == {"learnerId": L}
    assert set(rels) == {
        ("Topic", TOPIC, "COVERS", "Skill", SKILL),
        ("Skill", SKILL, "REQUIRES", "Skill", SKILL_B),
        ("Evidence", EVIDENCE, "ON", "Skill", SKILL),
        ("Alias", SKILL_OLD, "ALIAS_OF", "Skill", SKILL),
        ("Alias", ALIAS_EARLIER, "ALIAS_OF", "Skill", SKILL),
    }
    assert rels[("Skill", SKILL, "REQUIRES", "Skill", SKILL_B)] == {
        "origin": "model",
        "confidence": 0.7,
    }


def test_skill_merged_keeps_the_tests_of_every_level_and_part() -> None:
    """`TESTS {level, part}` is told apart by its identity props (`Т-§3.4.3`, D-A-133): the
    target already tested at level 1 by a task does not swallow the old skill's level 2 of it."""
    _, rels = _run(
        [_event(50, "skill.merged", {"from_id": SKILL_OLD, "into_id": SKILL})],
        [_TOPIC, _SKILL, _SKILL_OLD, _TASK],
        [
            _rel(("Task", TASK), "TESTS", ("Skill", SKILL), level=1, part="main"),
            _rel(("Task", TASK), "TESTS", ("Skill", SKILL_OLD), level=1, part="main"),
            _rel(("Task", TASK), "TESTS", ("Skill", SKILL_OLD), level=2, part="main"),
            _rel(("Task", TASK), "TESTS", ("Skill", SKILL_OLD), level=1, part="b"),
        ],
    )
    assert {(k[4], k[5], k[6]) for k in rels if k[2] == "TESTS"} == {
        (SKILL, 1, "main"),
        (SKILL, 2, "main"),
        (SKILL, 1, "b"),
    }


def test_merge_into_a_missing_or_the_same_node_changes_nothing() -> None:
    graph = ([_SKILL, _ALIAS], [_ALIAS_OF])
    before = _run([], *graph)
    assert (
        _run([_event(51, "skill.merged", {"from_id": SKILL_OLD, "into_id": SKILL})], *graph)
        == before
    )
    assert (
        _run([_event(52, "skill.merged", {"from_id": SKILL, "into_id": SKILL_B})], *graph) == before
    )


def test_topic_merged_moves_skills_and_hub_membership() -> None:
    """The target keeps its own place in the domain and its own levels in a hub it is in."""
    nodes, rels = _run(
        [_event(53, "topic.merged", {"from_id": TOPIC_B, "into_id": TOPIC})],
        [_DOMAIN, _TOPIC, _TOPIC_B, _SKILL, _SKILL_B, _HUB],
        [
            _rel(("Domain", DOMAIN), "HAS_TOPIC", ("Topic", TOPIC)),
            _rel(("Domain", DOMAIN), "HAS_TOPIC", ("Topic", TOPIC_B)),
            _rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL)),
            _rel(("Topic", TOPIC_B), "COVERS", ("Skill", SKILL_B)),
            _rel(("Hub", HUB), "INCLUDES", ("Topic", TOPIC), requiredLevels=[3]),
            _rel(("Hub", HUB), "INCLUDES", ("Topic", TOPIC_B), requiredLevels=[2]),
        ],
    )
    assert ("Topic", TOPIC_B) not in nodes
    assert set(rels) == {
        ("Domain", DOMAIN, "HAS_TOPIC", "Topic", TOPIC),
        ("Topic", TOPIC, "COVERS", "Skill", SKILL),
        ("Topic", TOPIC, "COVERS", "Skill", SKILL_B),
        ("Hub", HUB, "INCLUDES", "Topic", TOPIC),
        ("Alias", TOPIC_B, "ALIAS_OF", "Topic", TOPIC),
    }
    assert rels[("Hub", HUB, "INCLUDES", "Topic", TOPIC)] == {"requiredLevels": [3]}


def test_misconception_merged_moves_manifestations_and_traps() -> None:
    target = _node("Misconception", OTHER, domainId=DOMAIN, statement="o", triggers=[],
                   targetedInSessionId="")  # fmt: skip
    nodes, rels = _run(
        [_event(54, "misconception.merged", {"from_id": MISCONCEPTION, "into_id": OTHER})],
        [
            _SKILL,
            _SKILL_B,
            _MISCONCEPTION,
            target,
            _EVIDENCE,
            NodeRecord("Option", OPTION, {"learnerId": L, "label": "B", "isCorrect": False}),
        ],
        [
            _rel(("Misconception", OTHER), "ABOUT", ("Skill", SKILL)),
            _rel(("Misconception", MISCONCEPTION), "ABOUT", ("Skill", SKILL_B)),
            _rel(("Evidence", EVIDENCE), "MANIFESTS", ("Misconception", MISCONCEPTION)),
            _rel(("Option", OPTION), "TRAPS", ("Misconception", MISCONCEPTION)),
        ],
    )
    assert ("Misconception", MISCONCEPTION) not in nodes
    assert set(rels) == {
        ("Misconception", OTHER, "ABOUT", "Skill", SKILL),
        ("Evidence", EVIDENCE, "MANIFESTS", "Misconception", OTHER),
        ("Option", OPTION, "TRAPS", "Misconception", OTHER),
        ("Alias", MISCONCEPTION, "ALIAS_OF", "Misconception", OTHER),
    }


def test_topic_split_distributes_skills() -> None:
    """A new part is a topic of the same domain with the origin and status of the split one; a
    part with the old id keeps the topic and takes the part's name (Т-§2.8)."""
    split = {"topic_id": TOPIC, "into": [
        {"topic_id": TOPIC, "name": "Fraction sums", "skill_ids": [SKILL]},
        {"topic_id": TOPIC_NEW, "name": "Common denominators", "skill_ids": [SKILL_B]},
    ]}  # fmt: skip
    nodes, rels = _run(
        [_event(55, "topic.split", split)],
        [_DOMAIN, _TOPIC, _SKILL, _SKILL_B],
        [
            _rel(("Domain", DOMAIN), "HAS_TOPIC", ("Topic", TOPIC)),
            _rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL)),
            _rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL_B)),
        ],
    )
    assert nodes[("Topic", TOPIC)] == _TOPIC.props | {"name": "Fraction sums"}
    assert nodes[("Topic", TOPIC_NEW)] == _TOPIC.props | {"name": "Common denominators"}
    assert set(rels) == {
        ("Domain", DOMAIN, "HAS_TOPIC", "Topic", TOPIC),
        ("Domain", DOMAIN, "HAS_TOPIC", "Topic", TOPIC_NEW),
        ("Topic", TOPIC, "COVERS", "Skill", SKILL),
        ("Topic", TOPIC_NEW, "COVERS", "Skill", SKILL_B),
    }


def test_topic_split_into_new_topics_hides_the_old_one() -> None:
    """The split topic is left without skills: hidden, not deleted (D-A-131)."""
    split = {"topic_id": TOPIC, "into": [
        {"topic_id": TOPIC_B, "name": "a", "skill_ids": [SKILL]},
        {"topic_id": TOPIC_NEW, "name": "b", "skill_ids": [SKILL_B]},
    ]}  # fmt: skip
    nodes, _ = _run([_event(56, "topic.split", split)], [_DOMAIN, _TOPIC, _SKILL, _SKILL_B],
                    [_rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL)),
                     _rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL_B))])  # fmt: skip
    assert nodes[("Topic", TOPIC)]["status"] == "hidden"


def test_skill_split_creates_the_parts_and_keeps_the_old_skill() -> None:
    """Old evidence stays on the old skill; the dispatcher re-extracts the episodes (Т-§2.8).
    A part takes the domain, status and origin of the split skill; no summary yet — `''`."""
    split = {"skill_id": SKILL, "into": [
        {"skill_id": SKILL_P1, "name": "same denominators", "topic_id": TOPIC},
        {"skill_id": SKILL_P2, "name": "unlike denominators", "topic_id": TOPIC_B},
    ]}  # fmt: skip
    nodes, rels = _run(
        [_event(57, "skill.split", split)],
        [_TOPIC, _TOPIC_B, _SKILL, _EVIDENCE],
        [
            _rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL)),
            _rel(("Evidence", EVIDENCE), "ON", ("Skill", SKILL)),
        ],
    )
    assert nodes[("Skill", SKILL)] == _SKILL.props
    part = {"learnerId": L, "domainId": DOMAIN, "summary": "", "status": "active",
            "origin": "session"}  # fmt: skip
    assert nodes[("Skill", SKILL_P1)] == part | {"name": "same denominators"}
    assert nodes[("Skill", SKILL_P2)] == part | {"name": "unlike denominators"}
    assert ("Topic", TOPIC, "COVERS", "Skill", SKILL_P1) in rels
    assert ("Topic", TOPIC_B, "COVERS", "Skill", SKILL_P2) in rels
    assert ("Evidence", EVIDENCE, "ON", "Skill", SKILL) in rels


def test_split_of_a_one_skill_topic_in_one_batch_needs_the_topics_first() -> None:
    """WP8.2 (D-A-226, question of B in #245): a topic with one skill is split by `topic.split`
    (the parts exist) and then `skill.split` (its parts land in them). The other order loses a
    part: `skill.split` skips a part whose topic is not there yet, and `topic.split` cannot
    cover a skill that does not exist."""
    topic_split = {"topic_id": TOPIC, "into": [
        {"topic_id": TOPIC, "name": "Same denominators", "skill_ids": [SKILL]},
        {"topic_id": TOPIC_NEW, "name": "Unlike denominators", "skill_ids": []},
    ]}  # fmt: skip
    skill_split = {"skill_id": SKILL, "into": [
        {"skill_id": SKILL_P1, "name": "same", "topic_id": TOPIC},
        {"skill_id": SKILL_P2, "name": "unlike", "topic_id": TOPIC_NEW},
    ]}  # fmt: skip
    start = ([_DOMAIN, _TOPIC, _SKILL], [_rel(("Topic", TOPIC), "COVERS", ("Skill", SKILL))])
    nodes, rels = _run(
        [_event(60, "topic.split", topic_split), _event(61, "skill.split", skill_split)], *start
    )
    assert ("Topic", TOPIC_NEW) in nodes and nodes[("Topic", TOPIC)]["status"] == "active"
    assert ("Topic", TOPIC, "COVERS", "Skill", SKILL_P1) in rels
    assert ("Topic", TOPIC_NEW, "COVERS", "Skill", SKILL_P2) in rels
    wrong, wrong_rels = _run(
        [_event(60, "skill.split", skill_split), _event(61, "topic.split", topic_split)], *start
    )
    assert ("Skill", SKILL_P2) not in wrong
    assert ("Topic", TOPIC_NEW, "COVERS", "Skill", SKILL_P2) not in wrong_rels


# --- tasks (T-P4, group 4; Т-§4.4, Т-§5.1, Т-§5.2, Т-§5.6) -----------------------------------

TASK = str(UUID(int=0x7A))
FAMILY = str(UUID(int=0xFA))


def _tests(*pairs: tuple[object, int]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for skill, level in pairs:
        ref = {"new_skill": skill} if isinstance(skill, dict) else {"skill_id": skill}
        out.append(ref | {"level": level})
    return out


_CHOICE = {"type": "choice", "options": [
    {"option_id": "A", "text": "1/2", "correct": True},
    {"option_id": "B", "text": "2/4", "correct": False},
    {"option_id": "C", "text": "1/4", "correct": False},
]}  # fmt: skip
_MARKUP = {
    "tests": _tests(
        (SKILL, 3),
        (SKILL, 4),
        ({"name": "Cross multiply", "topic_hint": " fractions"}, 2),
        ({"name": "cross  multiply ", "topic_hint": "fractions"}, 3),
        ({"name": "Lowest terms", "topic_hint": "Simplification"}, 1),
    ),
    "method_named": False,
    "answer": _CHOICE,
    "options": [
        {"option_id": "B", "misconception_id": MISCONCEPTION},
        {"option_id": "C", "hypothesis": "multiplies the denominators", "neighbor_idea": True},
    ],
    "parts": [{"part": "b", "answer": {"type": "number"}, "tests": _tests((SKILL_B, 2))}],
}
_TASK_CREATED = {"task_id": TASK, "family_id": FAMILY, "source": "teacher", "markup": _MARKUP,
                 "verification": "unchecked"}  # fmt: skip


def _task_graph() -> tuple[list[NodeRecord], list[RelRecord]]:
    return [_DOMAIN, _TOPIC, _SKILL, _SKILL_B, _MISCONCEPTION], [
        _rel(("Domain", DOMAIN), "HAS_TOPIC", ("Topic", TOPIC))
    ]


def test_task_created_with_markup_options_and_new_skills() -> None:
    """Т-§5.2 markup: `TESTS {level}` per pair (parts too); descriptions of new skills with one
    normalized name in a domain are one provisional skill; a hint without a topic of that name
    makes a temporary topic (Т-§4.4); a trap on a hypothesis is a misconception with status
    `unknown` until it shows (WP3.2)."""
    seq = 60
    nodes, rels = _run(
        [_event(seq, "task.created", _TASK_CREATED, domain_id=UUID(DOMAIN))], *_task_graph()
    )
    cross = deterministic_id(LEARNER, seq, "skill:cross multiply")
    lowest = deterministic_id(LEARNER, seq, "skill:lowest terms")
    simplification = deterministic_id(LEARNER, seq, "topic:simplification")
    hypothesis = deterministic_id(LEARNER, seq, "misconception:C")
    options = {o: deterministic_id(LEARNER, seq, f"option:{o}") for o in "ABC"}
    assert nodes[("Task", TASK)] == {
        "learnerId": L,
        "domainId": DOMAIN,
        "familyId": FAMILY,
        "source": "teacher",
        "verification": "unchecked",
        "methodNamed": False,
    }
    provisional = {"learnerId": L, "domainId": DOMAIN, "summary": "", "status": "provisional",
                   "origin": "session"}  # fmt: skip
    assert nodes[("Skill", cross)] == provisional | {"name": "Cross multiply"}
    assert nodes[("Skill", lowest)] == provisional | {"name": "Lowest terms"}
    assert nodes[("Topic", simplification)] == {
        "learnerId": L,
        "domainId": DOMAIN,
        "name": "Simplification",
        "origin": "session",
        "status": "proposed",
        "knownClaimedAtMs": 0,
    }
    tests = {(k[4], k[5], k[6]) for k in rels if k[2] == "TESTS"}
    assert tests == {(SKILL, 3, "main"), (SKILL, 4, "main"), (cross, 2, "main"),
                     (cross, 3, "main"), (lowest, 1, "main"), (SKILL_B, 2, "b")}  # fmt: skip
    assert rels[("Task", TASK, "TESTS", "Skill", SKILL, 4, "main")] == {"level": 4, "part": "main"}
    assert ("Topic", TOPIC, "COVERS", "Skill", cross) in rels
    assert ("Topic", simplification, "COVERS", "Skill", lowest) in rels
    assert ("Domain", DOMAIN, "HAS_TOPIC", "Topic", simplification) in rels
    assert [nodes[("Option", options[o])]["label"] for o in "ABC"] == ["A", "B", "C"]
    assert nodes[("Option", options["A"])] == {"learnerId": L, "label": "A", "isCorrect": True,
                                               "neighborIdea": False}  # fmt: skip
    assert nodes[("Option", options["C"])]["neighborIdea"] is True
    assert all(("Task", TASK, "HAS_OPTION", "Option", options[o]) in rels for o in "ABC")
    assert ("Option", options["B"], "TRAPS", "Misconception", MISCONCEPTION) in rels
    assert nodes[("Misconception", hypothesis)] == {
        "learnerId": L,
        "domainId": DOMAIN,
        "statement": "multiplies the denominators",
        "status": "unknown",
        "triggers": [],
        "targetedInSessionId": "",
        "searchable": True,
    }
    assert ("Option", options["C"], "TRAPS", "Misconception", hypothesis) in rels
    assert ("Misconception", hypothesis, "ABOUT", "Skill", SKILL) in rels  # first tested skill


def test_a_description_with_the_name_of_a_skill_of_the_domain_is_that_skill() -> None:
    markup = _MARKUP | {"tests": _tests(({"name": " ADD fractions", "topic_hint": "x"}, 3)),
                        "options": None, "parts": None, "answer": {"type": "number"}}  # fmt: skip
    nodes, rels = _run(
        [_event(61, "task.created", _TASK_CREATED | {"markup": markup}, domain_id=UUID(DOMAIN))],
        *_task_graph(),
    )
    assert ("Task", TASK, "TESTS", "Skill", SKILL, 3, "main") in rels
    assert [key for key in nodes if key[0] == "Skill"] == [("Skill", SKILL), ("Skill", SKILL_B)]


def test_task_checks_set_verification_and_correct_the_markup() -> None:
    """Т-§5.6: `task.verified` — checked; `task.markup_disputed` / `task.key_disputed` —
    disputed, the corrected markup replaces the old one (the verdicts are lowered to class
    model: `test_a_dispute_lowers_the_verdicts_already_given_to_class_model`)."""
    created = _event(62, "task.created", _TASK_CREATED, domain_id=UUID(DOMAIN))
    corrected = _MARKUP | {"tests": _tests((SKILL, 3)), "parts": None}
    nodes, rels = _run(
        [
            created,
            _event(63, "task.verified", {"task_id": TASK}),
            _event(64, "task.markup_disputed", {"task_id": TASK, "corrected_markup": corrected}),
        ],
        *_task_graph(),
    )
    assert nodes[("Task", TASK)]["verification"] == "disputed"
    assert {(k[4], k[5], k[6]) for k in rels if k[2] == "TESTS"} == {(SKILL, 3, "main")}
    nodes, _ = _run([created, _event(65, "task.verified", {"task_id": TASK})], *_task_graph())
    assert nodes[("Task", TASK)]["verification"] == "checked"


# --- events in the slice (T-P3; Т-§2.4: folds read events through the view) ------------------

ANSWER_SEQ = 70


def _answer(seq: int = ANSWER_SEQ, **payload: Any) -> StoredEvent:
    body = {"task_id": TASK, "part": None, "answer": {"kind": "choice", "value": "A"},
            "source": "chat"} | payload  # fmt: skip
    return _event(seq, "answer.submitted", body, domain_id=UUID(DOMAIN), session_id=SESSION)


def test_handler_reads_only_the_events_it_needs() -> None:
    """An event of the learner read by a handler is declared in `needs()`; a node named in that
    event's payload can be declared through it (`(seq, field, label)`), with its relations."""
    task = _node("Task", TASK, domainId=DOMAIN)
    answer = _answer()
    batch = BatchSlice(L, SubGraph(nodes=[task, _SKILL], rels=[]), events=[answer])
    need = Need(
        events=frozenset({ANSWER_SEQ}), event_refs=frozenset({(ANSWER_SEQ, "task_id", "Task")})
    )
    view = batch.view(need)
    assert view.event(ANSWER_SEQ) == answer
    assert view.node("Task", TASK) == task.props
    assert view.out_rels("Task", TASK) == []
    with pytest.raises(OutsideSliceError):
        view.event(ANSWER_SEQ + 1)
    with pytest.raises(OutsideSliceError):
        batch.view(Need()).event(ANSWER_SEQ)
    with pytest.raises(OutsideSliceError):
        batch.view(Need(events=frozenset({ANSWER_SEQ}))).node("Task", TASK)


def test_events_of_the_batch_are_seen_by_later_events() -> None:
    """E.g. an answer and its verdict in one batch: the verdict handler sees the answer event;
    an event without a handler is seen too."""
    batch = BatchSlice(L, SubGraph())
    apply_events([_event(ANSWER_SEQ, "message.sent", {"text": "is it 1/2?"})], batch)
    view = batch.view(Need(events=frozenset({ANSWER_SEQ})))
    assert view.event(ANSWER_SEQ) is not None


# --- verdicts, cards, typos, disputes (T-P4, group 5; Т-§7.2, Т-§7.8, Т-§9.1, Т-§2.8) --------

VERDICT_SEQ = 71
OPTION_A, OPTION_B = str(UUID(int=0x0A)), str(UUID(int=0x0B))
_TASK = _node("Task", TASK, domainId=DOMAIN, familyId=FAMILY, source="teacher",
              verification="checked", methodNamed=False)  # fmt: skip
_TRAP_MISCONCEPTION = _node("Misconception", MISCONCEPTION, domainId=DOMAIN, statement="s",
                            status="suspected", triggers=[], targetedInSessionId="")  # fmt: skip


def _tests_rel(skill: str, level: int, part: str = "main") -> RelRecord:
    return _rel(("Task", TASK), "TESTS", ("Skill", skill), level=level, part=part)


def _verdict_graph(task: NodeRecord = _TASK) -> tuple[list[NodeRecord], list[RelRecord]]:
    option_a = NodeRecord("Option", OPTION_A, {"learnerId": L, "label": "A", "isCorrect": True})
    option_b = NodeRecord("Option", OPTION_B, {"learnerId": L, "label": "B", "isCorrect": False})
    nodes = [_DOMAIN, _SKILL, _SKILL_B, task, _TRAP_MISCONCEPTION, option_a, option_b]
    rels = [
        _tests_rel(SKILL, 3),
        _tests_rel(SKILL, 4),
        _tests_rel(SKILL_B, 2, "b"),
        _rel(("Task", TASK), "HAS_OPTION", ("Option", OPTION_A)),
        _rel(("Task", TASK), "HAS_OPTION", ("Option", OPTION_B)),
        _rel(("Option", OPTION_B), "TRAPS", ("Misconception", MISCONCEPTION)),
        _rel(("Misconception", MISCONCEPTION), "ABOUT", ("Skill", SKILL)),
    ]
    return nodes, rels


def _verdict(**changes: Any) -> StoredEvent:
    body = {"answer_seq": ANSWER_SEQ, "part": "main", "outcome": "incorrect", "method": "exact",
            "verification": "code"} | changes  # fmt: skip
    return _event(VERDICT_SEQ, "verdict.recorded", body, domain_id=UUID(DOMAIN),
                  session_id=SESSION)  # fmt: skip


def _evidence_props(**changes: Any) -> Props:
    """Phase A writes every property of an Evidence explicitly (law 8); weight, novelty and
    delayed are the fold's (phase B), here their «not counted yet» 0 / false."""
    return {
        "learnerId": L, "domainId": DOMAIN, "atMs": AT_MS, "level": 3, "outcome": "incorrect",
        "assistance": "none", "hintStep": 0, "novelty": 0.0, "confidence": 0, "tier": 1,
        "weight": 0.0, "verification": "code", "delayed": False, "solveTimeMs": 0,
        "gapType": "unknown", "quote": "", "sourceSessionId": str(SESSION),
        "sessionDomainId": DOMAIN, "sourceSeq": ANSWER_SEQ, "derivedKind": "direct",
        "status": "active", "observationSeq": 0, "extractor": "", "episodeSeq": 0,
        "searchable": False,
    } | changes  # fmt: skip


def _ev(n: int, seq: int = VERDICT_SEQ) -> str:
    return deterministic_id(LEARNER, seq, f"evidence:{n}")


def test_code_verdict_gives_t1_evidence_per_pair_of_its_part() -> None:
    """Т-§7.8 п.1–2: a code verdict on a marked task — T1 per pair of the part's markup, with
    the verdict's outcome; a wrong option with a trap — a T1 manifestation (`MANIFESTS`)."""
    nodes, rels = _run(
        [_answer(confidence=3), _verdict(help_step=1, misconception_id=MISCONCEPTION)],
        *_verdict_graph(),
    )
    hint = {"assistance": "hint", "hintStep": 1, "confidence": 3}
    assert nodes[("Evidence", _ev(0))] == _evidence_props(**hint)
    assert nodes[("Evidence", _ev(1))] == _evidence_props(level=4, **hint)
    assert {k for k in rels if k[0] == "Evidence"} == {
        ("Evidence", _ev(0), "ON", "Skill", SKILL),
        ("Evidence", _ev(0), "ON_TASK", "Task", TASK),
        ("Evidence", _ev(1), "ON", "Skill", SKILL),
        ("Evidence", _ev(1), "ON_TASK", "Task", TASK),
        ("Evidence", _ev(0), "MANIFESTS", "Misconception", MISCONCEPTION),
    }


def test_verdict_of_a_part_uses_its_markup() -> None:
    nodes, _ = _run([_answer(part="b"), _verdict(part="b", outcome="correct")], *_verdict_graph())
    assert nodes[("Evidence", _ev(0))] == _evidence_props(level=2, outcome="correct",
                                                          gapType="none")  # fmt: skip
    assert ("Evidence", _ev(1)) not in nodes


@pytest.mark.parametrize(
    ("verdict", "task_verification", "tier", "verification"),
    [
        ({"verification": "model", "method": "model"}, "checked", 2, "model"),
        (
            {
                "verification": "model",
                "method": "model",
                "second_opinion": {"requested": True, "agreed": True},
            },
            "checked",
            2,
            "model",
        ),
        (
            {
                "verification": "model",
                "method": "model",
                "second_opinion": {"requested": True, "agreed": False},
            },
            "checked",
            3,
            "model",
        ),
        ({"verification": "code"}, "disputed", 2, "model"),
    ],
)
def test_tier_by_verification_and_second_opinion(
    verdict: dict[str, Any], task_verification: str, tier: int, verification: str
) -> None:
    """Т-§7.2, Т-§7.8 п.3: model — T2, or T3 if the second opinion disagreed; a disputed task
    lowers its verdicts to class model (Т-§5.6)."""
    task = _node("Task", TASK, **(_TASK.props | {"verification": task_verification}))
    nodes, _ = _run([_answer(), _verdict(**verdict)], *_verdict_graph(task))
    evidence = nodes[("Evidence", _ev(0))]
    assert (evidence["tier"], evidence["verification"]) == (tier, verification)


@pytest.mark.parametrize("check", ["task.key_disputed", "task.markup_disputed"])
def test_a_dispute_lowers_the_verdicts_already_given_to_class_model(check: str) -> None:
    """Т-§5.6, D-A-132: `task.key_disputed` / `task.markup_disputed` — the verdicts already
    given (and the evidence derived from them) go down to class model, T1 to T2, exactly as a
    verdict recorded after the dispute (the same handlers in every mode, T-P1); other checks
    and other evidence stay."""
    requires = _rel(("Skill", SKILL), "REQUIRES", ("Skill", SKILL_B), origin="model",
                    confidence=0.9)  # fmt: skip
    graph_nodes, graph_rels = _verdict_graph()
    graph = (graph_nodes, [*graph_rels, requires])
    disputed_nodes, _ = _verdict_graph(
        _node("Task", TASK, **(_TASK.props | {"verification": "disputed"}))
    )
    card = _answer(72, source="card", self_assessment=True)
    given = [_answer(), _verdict(outcome="correct"), card]

    nodes, _ = _run([*given, _event(80, check, {"task_id": TASK})], *graph)
    lowered = {k: v for k, v in nodes.items() if k[0] == "Evidence"}
    assert any(v["derivedKind"] != "direct" for v in lowered.values())  # derived is covered
    assert {(v["verification"], v["tier"]) for k, v in lowered.items()
            if k[1] != _ev(0, 72)} == {("model", 2)}  # fmt: skip
    assert lowered[("Evidence", _ev(0, 72))]["tier"] == 4  # the card stays T4

    recorded_after, _ = _run(given, disputed_nodes, graph[1])
    assert lowered == {k: v for k, v in recorded_after.items() if k[0] == "Evidence"}

    nodes, _ = _run([*given, _event(80, "task.verified", {"task_id": TASK})], *graph)
    assert nodes[("Evidence", _ev(0))]["verification"] == "code"


def test_unclear_verdict_or_missing_answer_gives_nothing() -> None:
    graph = _verdict_graph()
    before = _run([_answer()], *graph)
    assert _run([_answer(), _verdict(outcome="unclear")], *graph) == before
    assert _run([_verdict()], *graph) == _run([], *graph)  # the answer event is gone (Т-§2.9)


def test_resist_and_manifest_sources() -> None:
    """T-MS2 (`Т-§9.1`, `Т-§7.8` п.2, п.7): a wrong trap option — a T1 manifestation by code; a
    hypothesis of a model verdict — a manifestation with its own tier; a success passing the
    gate (T1/T2, no shown solution) on a task where an option catches a misconception —
    `RESISTED` to it."""
    nodes, rels = _run([_answer(), _verdict(misconception_id=MISCONCEPTION)], *_verdict_graph())
    assert ("Evidence", _ev(0), "MANIFESTS", "Misconception", MISCONCEPTION) in rels
    assert nodes[("Evidence", _ev(0))]["tier"] == 1
    disagreed = _verdict(verification="model", method="model", misconception_hypothesis="h",
                         second_opinion={"requested": True, "agreed": False})  # fmt: skip
    nodes, rels = _run([_answer(), disagreed], *_verdict_graph())
    assert any(k[2] == "MANIFESTS" for k in rels) and nodes[("Evidence", _ev(0))]["tier"] == 3
    _, rels = _run([_answer(), _verdict(outcome="correct")], *_verdict_graph())
    assert ("Evidence", _ev(0), "RESISTED", "Misconception", MISCONCEPTION) in rels
    _, rels = _run([_answer(), _verdict(outcome="correct", solution_shown=True)], *_verdict_graph())
    assert not any(k[2] == "RESISTED" for k in rels)


def test_card_self_assessment_is_t4_evidence_of_level_1() -> None:
    """D-A-125 п.1: a card with a self-assessment — T4 on the skills of the card markup, level
    1, outcome by the self-assessment; other answers change nothing."""
    card = _answer(source="card", self_assessment=False)
    nodes, rels = _run([card], *_verdict_graph())
    props = _evidence_props(level=1, outcome="incorrect", tier=4, verification="model",
                            sourceSeq=ANSWER_SEQ)  # fmt: skip
    assert nodes[("Evidence", _ev(0, ANSWER_SEQ))] == props
    assert ("Evidence", _ev(0, ANSWER_SEQ), "ON", "Skill", SKILL) in rels
    assert ("Evidence", _ev(1, ANSWER_SEQ)) not in nodes  # one per skill, level 1
    assert _run([_answer()], *_verdict_graph()) == _run([], *_verdict_graph())


def test_typo_declared_disputes_the_verdict_evidence_as_slip() -> None:
    """Т-§2.8: «описка» — the verdict's evidence is excluded with what is derived from it; its
    gap type is `slip` (WP3.2)."""
    typo = _event(72, "typo.declared", {"answer_seq": ANSWER_SEQ, "part": "main"})
    nodes, _ = _run([_answer(), _verdict(), typo], *_verdict_graph())
    for n in (0, 1):
        assert nodes[("Evidence", _ev(n))] == _evidence_props(
            level=3 + n, status="disputed", gapType="slip"
        )


def test_dispute_of_evidence_misconception_or_verdict() -> None:
    """Т-§2.8: the target gets `disputed` together with the evidence derived from it. A verdict
    is named like its key `verdict:{answer_seq}:{part}` — `"<answer_seq>:<part>"` (D-A-134)."""
    derived = _ev(9)
    graph_nodes, graph_rels = _verdict_graph()
    nodes, _ = _run(
        [
            _answer(),
            _verdict(),
            _event(
                73, "dispute.filed", {"target_kind": "verdict", "target_id": f"{ANSWER_SEQ}:main"}
            ),
        ],
        graph_nodes,
        graph_rels,
    )
    assert [nodes[("Evidence", _ev(n))]["status"] for n in (0, 1)] == ["disputed", "disputed"]
    source = NodeRecord("Evidence", _ev(0), _evidence_props())
    child = NodeRecord("Evidence", derived, _evidence_props(derivedKind="indirect"))
    nodes, _ = _run(
        [
            _event(74, "dispute.filed", {"target_kind": "evidence", "target_id": _ev(0)}),
            _event(
                75, "dispute.filed", {"target_kind": "misconception", "target_id": MISCONCEPTION}
            ),
        ],
        [*graph_nodes, source, child],
        [*graph_rels, _rel(("Evidence", derived), "DERIVED_FROM", ("Evidence", _ev(0)))],
    )
    assert nodes[("Evidence", _ev(0))]["status"] == "disputed"
    assert nodes[("Evidence", derived)]["status"] == "disputed"
    assert nodes[("Misconception", MISCONCEPTION)]["status"] == "disputed"


def test_model_verdict_hypothesis_is_a_manifestation_of_a_new_misconception() -> None:
    """Т-§9.1: a hypothesis of a model verdict — a manifestation with its own tier; the
    misconception is `unknown` until its fold, `ABOUT` the first skill (D-A-134)."""
    verdict = _verdict(verification="model", method="model",
                       misconception_hypothesis="adds the denominators")  # fmt: skip
    nodes, rels = _run([_answer(), verdict], *_verdict_graph())
    new = deterministic_id(LEARNER, VERDICT_SEQ, "misconception")
    assert nodes[("Misconception", new)] == {
        "learnerId": L,
        "domainId": DOMAIN,
        "statement": "adds the denominators",
        "status": "unknown",
        "triggers": [],
        "targetedInSessionId": "",
        "searchable": True,
    }
    assert ("Misconception", new, "ABOUT", "Skill", SKILL) in rels
    assert ("Evidence", _ev(0), "MANIFESTS", "Misconception", new) in rels
    assert nodes[("Evidence", _ev(0))]["tier"] == 2


# --- the teacher's turn, closed tasks, observations (T-P4, group 6; Т-§7.8, Т-§9.1, Т-§8.4) ---

OBS_SEQ = 80


def _turn(seq: int = 76) -> StoredEvent:
    body = sample_payload("turn.committed")
    markup = body["markup"]
    markup["exposures"] = [{"skill_id": SKILL, "kind": "explained", "approach": "example_first"},
                           {"skill_id": str(UUID(int=0x99)), "kind": "example",
                            "approach": "example_first"}]  # fmt: skip
    markup["targets_misconception_id"] = MISCONCEPTION
    markup["tasks"][0]["task_id"] = TASK
    return _event(seq, "turn.committed", body, domain_id=UUID(DOMAIN), session_id=SESSION)


def test_turn_committed_exposures_and_targeting() -> None:
    """Т-§7.8 п.6, Т-§9.1: exposures of the markup — `Exposure` nodes `OF` their skill (a skill
    that is gone — no exposure); targeting — the session on the misconception and the target on
    the tasks of the turn (for `RESISTED`, D-A-134 п.4)."""
    nodes, rels = _run([_turn()], *_verdict_graph())
    exposure = deterministic_id(LEARNER, 76, "exposure:0")
    assert nodes[("Exposure", exposure)] == {
        "learnerId": L,
        "atMs": AT_MS,
        "kind": "explained",
        "approach": "example_first",
        "sourceSeq": 76,
        "sessionDomainId": DOMAIN,
    }
    assert ("Exposure", exposure, "OF", "Skill", SKILL) in rels
    assert ("Exposure", deterministic_id(LEARNER, 76, "exposure:1")) not in nodes
    assert nodes[("Misconception", MISCONCEPTION)]["targetedInSessionId"] == str(SESSION)
    assert nodes[("Task", TASK)]["targetsMisconceptionId"] == MISCONCEPTION


def test_task_closed_records_how_for_the_consolidation_fold() -> None:
    """Т-§8.4: a solution shown on request — consolidation, through the fold (WP4.5)."""
    closed = _event(77, "task.closed", {"task_id": TASK, "how": "solution_shown"})
    nodes, _ = _run([closed], *_verdict_graph())
    assert nodes[("Task", TASK)] == _TASK.props | {"closedHow": "solution_shown",
                                                   "closedAtMs": AT_MS}  # fmt: skip


def _observation(seq: int = OBS_SEQ, extractor: str = "extractor@3", **element: Any) -> StoredEvent:
    body = sample_payload("observation.extracted")
    body["extractor"] = extractor
    body["elements"] = [body["elements"][0] | {
        "skill_id": SKILL, "outcome": "incorrect", "help": "hint", "hint_step": 2,
        "gap_type": "execution", "quote": "a/b", "events": [5, 7], "at_ms": 1000,
        "root_cause": {"skill_id": SKILL_B, "confidence": "high"},
    } | element]  # fmt: skip
    return _event(seq, "observation.extracted", body, domain_id=UUID(DOMAIN), session_id=SESSION)


def test_observation_extracted_gives_t3_evidence_and_supersedes_the_earlier_one() -> None:
    """Т-§7.8 п.4, Т-§2.8, Т-§9.4: elements — T3 evidence with the quote, help and time of the
    source events; the earlier observation's evidence of the episode — `superseded`; a root
    cause — `ROOT_CAUSE {confidence}` by `root_cause_confidence_map`."""
    earlier = NodeRecord("Evidence", _ev(0, 60), _evidence_props(observationSeq=60, episodeSeq=9,
                                                                 tier=3))  # fmt: skip
    nodes, rels = _run([_observation()], [*_verdict_graph()[0], earlier], _verdict_graph()[1])
    evidence = nodes[("Evidence", _ev(0, OBS_SEQ))]
    assert evidence == _evidence_props(
        outcome="incorrect",
        assistance="hint",
        hintStep=2,
        gapType="execution",
        quote="a/b",
        atMs=1000,
        sourceSeq=7,
        tier=3,
        verification="model",
        observationSeq=OBS_SEQ,
        extractor="extractor@3",
        episodeSeq=9,
        searchable=True,
    )
    assert ("Evidence", _ev(0, OBS_SEQ), "ON", "Skill", SKILL) in rels
    assert rels[("Evidence", _ev(0, OBS_SEQ), "ROOT_CAUSE", "Skill", SKILL_B)] == {
        "confidence": 0.9
    }
    assert nodes[("Evidence", _ev(0, 60))]["status"] == "superseded"


def test_observation_of_another_extractor_or_unclear_skill_changes_nothing() -> None:
    """`extractor_precedence = ["extractor@3"]` (WP3.6); «неясно» — no evidence (`Т-§7.6`)."""
    graph = _verdict_graph()
    assert _run([_observation(extractor="extractor@2")], *graph) == _run([], *graph)
    unclear = _observation(skill_id=None, skill_unclear=True)
    assert _run([unclear], *graph) == _run([], *graph)


def test_extracted_hypothesis_is_a_manifestation() -> None:
    hypothesis = _observation(kind="misconception_hypothesis", level=None, outcome=None,
                              help="none", hint_step=None, gap_type="misconception",
                              new_misconception="adds denominators", root_cause=None)  # fmt: skip
    nodes, rels = _run([hypothesis], *_verdict_graph())
    new = deterministic_id(LEARNER, OBS_SEQ, "misconception:0")
    assert nodes[("Misconception", new)]["status"] == "unknown"
    assert ("Evidence", _ev(0, OBS_SEQ), "MANIFESTS", "Misconception", new) in rels
    assert nodes[("Evidence", _ev(0, OBS_SEQ))]["level"] == 0


def _diagnosed(**diagnosis: Any) -> StoredEvent:
    raw = sample_payload("observation.diagnosed")
    base = raw["diagnosis"]
    assert isinstance(base, dict)
    body = raw | {"task_id": TASK, "diagnosis": {**base, **diagnosis}}
    return _event(81, "observation.diagnosed", body, domain_id=UUID(DOMAIN), session_id=SESSION)


def test_canvas_diagnosis_is_level_0_evidence() -> None:
    """D-A-125 п.2: level 0, incorrect, T3 — on the skill of the named misconception, else on
    the skills the task tests; `MANIFESTS`; `ROOT_CAUSE` with the diagnosis confidence."""
    nodes, rels = _run([_diagnosed(misconception_id=MISCONCEPTION, root_cause_skill_id=SKILL_B)],
                       *_verdict_graph())  # fmt: skip
    first = _ev(0, 81)
    assert nodes[("Evidence", first)] == _evidence_props(
        level=0,
        tier=3,
        verification="model",
        gapType="execution",
        sourceSeq=81,
        observationSeq=81,
    )
    assert {k for k in rels if k[0] == "Evidence"} == {
        ("Evidence", first, "ON", "Skill", SKILL),
        ("Evidence", first, "ON_TASK", "Task", TASK),
        ("Evidence", first, "MANIFESTS", "Misconception", MISCONCEPTION),
        ("Evidence", first, "ROOT_CAUSE", "Skill", SKILL_B),
    }
    nodes, _ = _run([_diagnosed()], *_verdict_graph())
    levels = [nodes[("Evidence", _ev(n, 81))]["level"] for n in (0, 1)]
    assert levels == [0, 0] and ("Evidence", _ev(2, 81)) not in nodes  # SKILL and SKILL_B


def test_second_opinion_raises_an_agreed_diagnosis_to_t2() -> None:
    """D-A-125 п.3 (Т-§7.7): agreed — T3 -> T2; disagreed — stays T3."""
    diagnosis = _diagnosed(misconception_id=MISCONCEPTION)
    for agreed, tier in ((True, 2), (False, 3)):
        opinion = _event(82, "observation.second_opinion", {"target_seq": 81, "agreed": agreed})
        nodes, _ = _run([diagnosis, opinion], *_verdict_graph())
        assert nodes[("Evidence", _ev(0, 81))]["tier"] == tier


def test_derived_evidence() -> None:
    """T-F7 (WP4.6, `Т-§7.9`): from a direct positive evidence, one step: `indirect` over
    `REQUIRES` with enough confidence, `transfer` over `SAME_IDEA` in either direction; level
    `min(3, source)`, `DERIVED_FROM` the source; nothing from a failure or a weak relation."""
    third, weak = str(UUID(int=0x53)), str(UUID(int=0x54))
    nodes, rels = _verdict_graph()
    nodes += [
        _node("Skill", third, domainId=DOMAIN, name="t", summary="s", status="active",
              origin="session"),
        _node("Skill", weak, domainId=DOMAIN, name="w", summary="s", status="active",
              origin="session"),
    ]  # fmt: skip
    rels += [
        _rel(("Skill", SKILL), "REQUIRES", ("Skill", SKILL_B), origin="model", confidence=0.9),
        _rel(("Skill", third), "SAME_IDEA", ("Skill", SKILL), confidence=0.8),
        _rel(("Skill", SKILL), "REQUIRES", ("Skill", weak), origin="model", confidence=0.1),
    ]
    graph, links = _run([_answer(), _verdict(outcome="correct")], nodes, rels)
    derived = {
        str(k[1]): graph[("Evidence", str(k[1]))]
        for k in links
        if k[0] == "Evidence" and k[2] == "DERIVED_FROM"
    }
    found = sorted(
        (str(p["derivedKind"]), cast(int, p["level"]), k[4])
        for e, p in derived.items()
        for k in links
        if k[:3] == ("Evidence", e, "ON")
    )
    # sources at levels 3 and 4 give one derived evidence of level 3 per target, not two
    assert found == [("indirect", 3, SKILL_B), ("transfer", 3, third)]
    assert all(p["tier"] == 1 and p["outcome"] == "correct" for p in derived.values())
    _, links = _run([_answer(), _verdict(outcome="incorrect")], nodes, rels)
    assert not [k for k in links if k[2] == "DERIVED_FROM"]
    removed = _event(80, "edge.removed", {"kind": "requires", "from_id": SKILL, "to_id": SKILL_B})
    graph, links = _run([_answer(), _verdict(outcome="correct"), removed], nodes, rels)
    status = {
        str(graph[("Evidence", str(k[1]))]["derivedKind"]): graph[("Evidence", str(k[1]))]["status"]
        for k in links
        if k[2] == "DERIVED_FROM"
    }
    assert status == {"indirect": "superseded", "transfer": "active"}  # [РЕШЕНИЕ Ф4] WP4.6
