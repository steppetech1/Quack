"""The service turn of the built-in `/skill-creator` (owner decision 2026-11-24).

The learner calls `/skill-creator <what I want>` in the teacher chat; the teacher drafts a learner
skill with them in a few short turns and proposes it as the element `skill_proposal`. Code decides
everything around that (law 2, the model only proposes):

- **the mode** is read from the journal, never from the model: a message is a service message when
  it calls the command, or when it follows a service turn that is still open (`dialogue_open`);
- **the output** of a service turn is settled by code (`settle_service_output`): no task, no
  exposure, no plan update, no agent request, no deadline, no skill label, and nothing but a
  proposal as an element. Outside the mode a proposal is dropped (`drop_proposals`);
- **the proposal** is settled by code too: the name becomes a valid skill name that is not
  reserved, the text is cut to `learner_skill_max_chars` (`settle_proposal`). The learner reviews
  and saves it from the browser; the model never writes the skill store.

A turn is *open* when it asked a clarifying question (`head.intent == "clarify_goal"`) or made a
proposal: the next plain message of the learner goes to the same dialogue. A turn that is neither
closes it, so the learner can always leave by saying so; `skill_creator_dialogue_turns_max` caps a
dialogue.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from quack.config import Settings
from quack.db.db import Db
from quack.events.service_turns import (
    BUILTIN_SKILL_CREATOR,
    RESERVED_SKILL_NAMES,
    SERVICE_SKILL_CREATOR,
)
from quack.session.elements import SkillProposalElement
from quack.session.teacher_output import (
    BodyBlock,
    PlanUpdate,
    TeacherOutputV2,
    TurnBody,
    TurnMarkup,
)

# Mirrors `artifacts.learner_skills.NAME_PATTERN` (the session package does not import
# `artifacts`); a test keeps the two equal.
SKILL_NAME_PATTERN = re.compile(r"^[\w-]{1,40}$")
SKILL_NAME_MAX = 40
# The artifact types a skill may be bound to (`SkillArtifactType`); a test keeps the list equal to
# `artifacts.learner_skills.ARTIFACT_TYPES`.
SKILL_ARTIFACT_TYPES: tuple[str, ...] = ("notes", "cards", "cheatsheet")
_FALLBACK_NAME = "my-skill"
_NOT_NAME = re.compile(r"[^\w-]+")

_LAST_TURNS = (
    "SELECT payload ->> 'service' AS service, payload -> 'head' ->> 'intent' AS intent, "
    "EXISTS (SELECT 1 FROM jsonb_array_elements(payload -> 'markup' -> 'elements') e "
    "WHERE e ->> 'kind' = 'skill_proposal') AS proposal "
    "FROM core.events WHERE learner_id = %(learner_id)s AND session_id = %(session_id)s "
    "AND type = 'turn.committed' ORDER BY seq DESC LIMIT %(limit)s"
)


def dialogue_open(last_turns: Sequence[dict[str, Any]], turns_max: int) -> bool:
    """Whether the next plain message of the learner belongs to a service dialogue. `last_turns` —
    the latest `turn.committed` of the session, newest first: `service`, `intent`, `proposal`."""
    if not last_turns or last_turns[0]["service"] is None:
        return False
    run = 0
    for turn in last_turns:
        if turn["service"] is None:
            break
        run += 1
    if run >= turns_max:
        return False
    newest = last_turns[0]
    return bool(newest["proposal"]) or newest["intent"] == "clarify_goal"


async def creator_dialogue_open(
    db: Db, *, learner_id: UUID, session_id: UUID, settings: Settings
) -> bool:
    """`dialogue_open` over the journal of the session."""
    rows = await db.fetch_all(
        _LAST_TURNS,
        {
            "learner_id": learner_id,
            "session_id": session_id,
            "limit": settings.skill_creator_dialogue_turns_max,
        },
    )
    return dialogue_open([dict(r) for r in rows], settings.skill_creator_dialogue_turns_max)


# --- the name and the text of a proposal ---


def settle_name(raw: str) -> str:
    """A valid skill name made from what the model wrote: lower case, no leading `/`, runs of other
    characters become `-`, at most 40 characters; the reserved name gets a suffix."""
    name = _NOT_NAME.sub("-", raw.strip().lower().removeprefix("/")).strip("-_")
    name = name[:SKILL_NAME_MAX].strip("-_") or _FALLBACK_NAME
    if name in RESERVED_SKILL_NAMES:
        name = f"{name[: SKILL_NAME_MAX - 2]}-2"
    return name


def settle_proposal(
    element: SkillProposalElement, settings: Settings
) -> SkillProposalElement | None:
    """The proposal with a valid name and a text within the limit; `None` — nothing to propose."""
    text = element.text.strip()
    if not text:
        return None
    return element.model_copy(
        update={
            "name": settle_name(element.name),
            "text": text[: settings.learner_skill_max_chars].rstrip(),
        }
    )


# --- the output of a turn ---


def _proposal_blocks(
    blocks: Sequence[BodyBlock], element: SkillProposalElement | None
) -> list[BodyBlock]:
    kept = [b for b in blocks if b.element_id is None]
    if element is None:
        return kept
    used = {b.block_id for b in blocks}
    number = len(blocks) + 1
    while f"b{number}" in used:
        number += 1
    return [*kept, BodyBlock(block_id=f"b{number}", text=None, element_id=element.element_id)]


def drop_proposals(output: TeacherOutputV2) -> TeacherOutputV2:
    """Outside the mode a `skill_proposal` goes, with its block (the teacher may not make one)."""
    elements = output.p3_markup.elements
    gone = {e.element_id for e in elements if e.kind == "skill_proposal"}
    if not gone:
        return output
    blocks = [b for b in output.p2_body.blocks if b.element_id not in gone]
    markup = output.p3_markup.model_copy(
        update={"elements": [e for e in elements if e.element_id not in gone]}
    )
    return output.model_copy(update={"p2_body": TurnBody(blocks=blocks), "p3_markup": markup})


def settle_service_output(output: TeacherOutputV2, settings: Settings) -> TeacherOutputV2:
    """The output of a service turn as code lets it stand (idempotent). The text stays; the
    markup keeps at most one settled proposal (shown by a block even if the model forgot to point
    one at it) and nothing that changes the learner's picture or the session: no task, exposure,
    plan update, agent request, deadline, misconception, correction or skill label."""
    proposal = next(
        (p for e in output.p3_markup.elements if (p := _settled(e, settings)) is not None), None
    )
    blocks = [
        b
        for b in output.p2_body.blocks
        if b.element_id is None or (proposal is not None and b.element_id == proposal.element_id)
    ]
    if proposal is not None and not any(b.element_id == proposal.element_id for b in blocks):
        blocks = _proposal_blocks(blocks, proposal)
    head = output.p1_head.model_copy(
        update={
            "target_skills": [],
            "topic_label": None,
            "verdict_stance": None,
            "branch_type": None,
            "approach": "none",
            "intent": "answer_question"
            if output.p1_head.intent == "split_topic"
            else output.p1_head.intent,
        }
    )
    markup = TurnMarkup.model_validate(
        {
            "tasks": [],
            "elements": [] if proposal is None else [proposal.model_dump()],
            "exposures": [],
            "targetsMisconception": None,
            "planUpdate": PlanUpdate(
                goal=None,
                current_step=None,
                next_steps=None,
                landmark_edits=None,
                branches=[],
                cancel_requests=[],
            ),
            "agentRequests": [],
            "correctionsAcknowledged": [],
            "decisionReason": output.p3_markup.decision_reason,
            "deadline": None,
        }
    )
    return output.model_copy(
        update={"p1_head": head, "p2_body": TurnBody(blocks=blocks), "p3_markup": markup}
    )


def _settled(element: Any, settings: Settings) -> SkillProposalElement | None:
    if not isinstance(element, SkillProposalElement):
        return None
    return settle_proposal(element, settings)


__all__ = [
    "BUILTIN_SKILL_CREATOR",
    "SERVICE_SKILL_CREATOR",
    "SKILL_NAME_PATTERN",
    "creator_dialogue_open",
    "dialogue_open",
    "drop_proposals",
    "settle_name",
    "settle_proposal",
    "settle_service_output",
]
