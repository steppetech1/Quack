"""Rows of the projector handlers of `rules@3` (ТЗ Ф4 WP3.2, seam S5; G1: the table).

A row per event type the projector handles (§6.3 column «Проектор»): who writes it, the payload
fields the handler reads, the graph changes and the source. The handlers are WP3.2 (G2):
`handle(event, view) -> mutations` with `needs(event)` (`Т-§2.5`). The `reads` paths are a
contract with the writers of the events: a test checks every path exists in the payload model,
so a field the projector reads cannot be renamed or dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type Stream = Literal["A", "B", "C"]


@dataclass(frozen=True)
class ProjectorRow:
    """`reads` — dotted paths into the payload (through nested models and list items)."""

    event_type: str
    writers: tuple[Stream, ...]
    reads: tuple[str, ...]
    changes: str
    source: str


_STREAMS: dict[str, Stream] = {"A": "A", "B": "B", "C": "C"}


def _row(
    event_type: str, writers: str, reads: tuple[str, ...], changes: str, source: str
) -> ProjectorRow:
    """`writers` — the stream letters of the §6.3 column «Пишет», e.g. `"BC"`."""
    return ProjectorRow(event_type, tuple(_STREAMS[w] for w in writers), reads, changes, source)


_MARKUP_TESTS = ("markup.tests.skill_id", "markup.tests.new_skill", "markup.tests.level")
_EXTRACTED = tuple(
    f"elements.{name}"
    for name in (
        "kind", "skill_id", "new_skill", "skill_unclear", "level", "outcome", "help",
        "hint_step", "gap_type", "misconception_id", "new_misconception", "root_cause",
        "quote", "events", "at_ms",
    )
)  # fmt: skip
_DIAGNOSIS = tuple(
    f"diagnosis.{name}"
    for name in ("kind", "gap_type", "misconception_id", "misconception_hypothesis",
                 "root_cause_skill_id")
)  # fmt: skip

ROWS: tuple[ProjectorRow, ...] = (
    # --- structure (Т-§4) ---
    _row("domain.created", "BA", ("domain_id", "name"),
         "Domain, OWNS; hub kind = domain_all (composition not stored)", "Т-§4.8"),
    _row("topic.proposed", "BC", ("topic_id", "domain_id", "name", "origin"),
         "Topic (origin, status = proposed), HAS_TOPIC", "Т-§4.3, Т-§4.7"),
    _row("topic.created", "BC", ("topic_id", "domain_id", "name", "origin"),
         "Topic (origin, status = active), HAS_TOPIC", "Т-§4.3, Т-§4.7"),
    _row("topic.renamed", "AB", ("topic_id", "name"), "Topic.name", "Т-§4.11"),
    _row("topic.hidden", "AB", ("topic_id",), "Topic.status = hidden", "Т-§4.11"),
    _row("topic.merged", "AB", ("from_id", "into_id"),
         "relations and evidence moved to the target, old node deleted, Alias", "Т-§2.8"),
    _row("topic.split", "AB", ("topic_id", "into"),
         "skills distributed between the topics by the event", "Т-§2.8"),
    _row("skill.created", "B",
         ("skill_id", "topic_id", "domain_id", "name", "summary", "status", "origin"),
         "Skill (status provisional | active), COVERS from the home topic", "Т-§4.1, Т-§4.4"),
    _row("skill.merged", "B", ("from_id", "into_id"),
         "relations and evidence moved to the target, old node deleted, Alias", "Т-§2.8"),
    _row("skill.split", "B", ("skill_id", "into"),
         "new skills; old evidence stays on the old skill", "Т-§2.8"),
    _row("edge.added", "B", ("kind", "from_id", "to_id", "origin", "confidence"),
         "REQUIRES {origin, confidence} or SAME_IDEA {confidence}", "Т-§4.6, Т-§4.2"),
    _row("edge.removed", "B", ("kind", "from_id", "to_id"),
         "relation removed; evidence derived over it -> superseded (WP4.6)", "Т-§4.6, Т-§4.2"),
    _row("hub.created", "ABC",
         ("hub_id", "domain_id", "name", "kind", "origin", "control_type"),
         "Hub, HAS_HUB", "Т-§4.8"),
    _row("hub.deadline_set", "ABC", ("hub_id", "deadline_at_ms"),
         "Hub.deadlineAtMs (null -> 0, «не было»)", "Т-§4.8"),
    _row("hub.topics_changed", "ABC", ("hub_id", "topics"),
         "INCLUDES {requiredLevels} (default from controlType, WP6.3)", "Т-§4.8"),
    # --- tasks and verdicts (Т-§5, Т-§6, Т-§7) ---
    _row("task.created", "BC",
         ("task_id", "family_id", "source", "verification", *_MARKUP_TESTS,
          "markup.options.option_id", "markup.options.misconception_id",
          "markup.options.hypothesis"),
         "Task, Option, TESTS {level}, HAS_OPTION, TRAPS; skill descriptions -> temporary "
         "skills and topics (Т-§4.4)", "Т-§5.1, Т-§5.2"),
    _row("task.verified", "B", ("task_id", "corrected_markup"), "Task.verification",
         "Т-§5.6"),
    _row("task.key_disputed", "B", ("task_id", "corrected_markup"),
         "Task.verification = disputed; evidence of its verdicts down to class model (refold)",
         "Т-§5.6"),
    _row("task.markup_disputed", "B", ("task_id", "corrected_markup"),
         "markup corrected, refold", "Т-§5.6"),
    _row("verdict.recorded", "B",
         ("answer_seq", "part", "outcome", "method", "verification", "second_opinion",
          "confidence", "help_step", "solution_shown", "misconception_id",
          "misconception_hypothesis"),
         "the task and part through the answer event answer_seq (read in the view, "
         "D-A-133); evidence per (skill, level) of the markup part; tier by Т-§7.8; ON_TASK; wrong "
         "trap option -> manifestation T1 (MANIFESTS); gate-passing success on a trap task "
         "or targetsMisconception -> RESISTED; unclear -> no evidence",
         "Т-§7.8, Т-§9.1"),
    _row("answer.submitted", "B", ("task_id", "part", "source", "self_assessment"),
         "source = card with self_assessment: evidence T4 on the card markup skills, level 1 "
         "(D-A-125 п.1); other answers change nothing — evidence comes from verdict.recorded",
         "Т-§14.3, Т-§17.5, Т-§7.2"),
    _row("task.closed", "BC", ("task_id", "how"),
         "how = solution_shown -> needsConsolidation, consolidationLevel, "
         "consolidationSinceMs, bypassCount (through the fold, WP4.5)", "Т-§8.4"),
    # --- observations (Т-§7.6, Т-§9, Т-§15.4) ---
    _row("observation.extracted", "B", ("episode_seq", "extractor", "trigger_seq", *_EXTRACTED),
         "current observation of the episode by extractor_precedence; earlier evidence of the "
         "episode -> superseded; valid elements -> evidence (WP4.1), hypotheses -> "
         "manifestations, skill_unclear -> no evidence; new skills -> temporary (Т-§4.4); "
         "roots -> ROOT_CAUSE (confidence by root_cause_confidence_map)",
         "Т-§7.6, Т-§7.8, Т-§9.3, Т-§9.4"),
    _row("observation.diagnosed", "C", ("task_id", "confidence", "method", *_DIAGNOSIS),
         "Evidence level 0, outcome incorrect, gapType, tier by method (model -> T3, "
         "symbolic -> T3) on the skill of the named misconception, else on the skills the "
         "task tests (D-A-125 п.2); misconception -> MANIFESTS (a hypothesis -> suspected "
         "node); root_cause_skill_id -> ROOT_CAUSE", "Т-§7.1, Т-§15.4, WP5.1"),
    _row("observation.second_opinion", "C", ("target_seq", "agreed"),
         "agreed: evidence of the target diagnosis T3 -> T2; disagreed: stays T3 "
         "(D-A-125 п.3)", "Т-§7.7, Т-§15.4, WP5.1"),
    # --- teacher turn and session (Т-§7.8, Т-§9.1) ---
    _row("turn.committed", "B",
         ("markup.exposures.skill_id", "markup.exposures.kind", "markup.exposures.approach",
          "markup.targets_misconception_id", "markup.tasks.tests.new_skill"),
         "Exposure per markup.exposures (Т-§7.8 п.6); Misconception.targetedInSessionId "
         "= session of the event; temporary skills from the markup (merged by normalized "
         "name with task.created of the same append)", "Т-§7.8, Т-§9.1"),
    _row("session.ended", "B", ("session_id",),
         "targetedInSessionId of this session removed", "Т-§9.1"),
    # --- edits (Т-§2.8, Т-§4.11, Т-§9.5) ---
    _row("dispute.filed", "A", ("target_kind", "target_id"),
         "target -> disputed (with what DERIVED_FROM it)", "Т-§2.8"),
    _row("typo.declared", "A", ("answer_seq", "part"),
         "evidence of the verdict of answer_seq and part -> disputed, gap type slip",
         "Т-§2.8, Т-§7.6"),
    _row("known.claimed", "A", ("topic_id",),
         "Topic.knownClaimedAtMs; proposals of the topic hidden (visibility, WP6.1)",
         "Т-§4.11"),
    _row("preference.set", "A", ("key", "value"), "Profile.pref*", "Т-§9.5"),
    # --- materials and vectors (Т-§3.4, Т-§10.5) ---
    _row("material.uploaded", "C", ("material_id", "mime", "session_id"), "Material",
         "Т-§3.4, Т-§4.3"),
    _row("material.parsed", "C", ("material_id", "pages_count"), "Material pages",
         "Т-§3.4"),
    _row("chunk.indexed", "C",
         ("chunk_id", "material_id", "page", "text", "notation", "teaches", "uses"),
         "Chunk (page, text, notation; learnerId, domainId from the envelope and searchable "
         "= has text: filter of chunk_emb), HAS_CHUNK, TEACHES, USES",
         "Т-§3.4, Т-§3.4.6, Т-§4.3"),
    _row("embedding.ready", "C", ("node_kind", "node_id", "model", "dim", "vector"),
         "embedding on the node (through Alias; no target -> empty application)",
         "Т-§2.4, Т-§1.7"),
    # --- agents (Т-§9.3, Т-§13.5) ---
    _row("digest.written", "B", ("domain_id", "text", "as_of_seq", "manner"),
         "Digest of the domain (text, asOfSeq), HAS_DIGEST; manner -> Profile (WP5.3)",
         "Т-§13.5, Т-§9.5"),
    _row("misconception.described", "B", ("misconception_id", "statement", "triggers"),
         "Misconception.statement, triggers", "Т-§9.3"),
    _row("misconception.merged", "B", ("from_id", "into_id"),
         "manifestations and resistances moved to the target, Alias", "Т-§2.8, Т-§9.3"),
)  # fmt: skip
ROW_BY_TYPE: dict[str, ProjectorRow] = {row.event_type: row for row in ROWS}
