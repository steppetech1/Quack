"""Export of a topic to Obsidian and Anki (ТЗ Ф4 WP23.7; Т-§19.7; D-C-240).

Notes become a Markdown vault in a zip: one file per topic named after the topic, YAML at the
top (domain, topic id, date, the sources the blocks cite), the blocks under their kind, formulas
as they are written (`$…$`), and `[[internal links]]` to the topics this one builds on and the
topics built on it (`REQUIRES` between topics, derived by the graph reader, Т-§4.6). Cards
become an Anki deck of the fixed cards (no adaptive generation, П-11.2): the front is what the
learner sees, the back is the answer of the task behind the card.

The builders are pure; `load_topic_info` and `anki_cards` read the learner's own rows only.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections.abc import Sequence
from datetime import date
from html import escape
from typing import Any
from uuid import UUID, uuid5

import genanki  # pyright: ignore[reportMissingTypeStubs]
from pydantic import BaseModel, ConfigDict

from quack.artifacts.documents import Artifact, Block, BlockKind
from quack.db import Db
from quack.events.values import AnswerSpec
from quack.graph.ids import QUACK_UUID_NS
from quack.graph.read import GraphReader
from quack.practice.task_store import get_task

_TOPIC_NAME = (
    "SELECT payload ->> 'name' AS name FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = ANY(%(types)s) "
    "AND payload ->> 'topic_id' = %(topic_id)s ORDER BY seq DESC LIMIT 1"
)
_DOMAIN_NAME = (
    "SELECT payload ->> 'name' AS name FROM core.events "
    "WHERE learner_id = %(learner_id)s AND type = 'domain.created' "
    "AND payload ->> 'domain_id' = %(domain_id)s ORDER BY seq DESC LIMIT 1"
)

# Headings of the vault by the kind of the block (П-11.1).
BLOCK_HEADINGS: dict[BlockKind, str] = {
    "summary": "Кратко",
    "definition": "Определение",
    "statement": "Утверждение",
    "why": "Почему так",
    "when_to_apply": "Когда применять",
    "worked_example": "Разобранный пример",
    "my_traps": "Мои ловушки",
    "links": "Связи в тексте",
    "custom": "Мои заметки",
}
_FORBIDDEN_IN_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]+')
_SPACES = re.compile(r"\s+")
_DISPLAY_MATH = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
_INLINE_MATH = re.compile(r"\$(.+?)\$", re.DOTALL)


class TopicInfo(BaseModel):
    """What the export says of the topic besides its blocks or cards."""

    model_config = ConfigDict(frozen=True)

    topic_id: UUID
    name: str
    domain_id: UUID | None
    domain_name: str | None
    builds_on: list[str]
    needed_for: list[str]


class AnkiCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    card_id: UUID
    kind: str
    prompt: str
    answer: str


def note_name(name: str, fallback: str) -> str:
    """A file name and a link target: the name of the topic without the characters Obsidian
    does not allow in a note name."""
    cleaned = _SPACES.sub(" ", _FORBIDDEN_IN_NAME.sub(" ", name)).strip(" .")
    return cleaned or fallback


# --- Obsidian ---------------------------------------------------------------------------------


def _yaml(value: object) -> str:
    """A JSON scalar or flow collection is also a YAML one."""
    return json.dumps(value, ensure_ascii=False)


def _cited(blocks: Sequence[Block]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for block in blocks:
        for source in block.sources:
            item = {"material_id": source.get("material_id"), "page": source.get("page")}
            if item not in found:
                found.append(item)
    return found


def _links(label: str, names: Sequence[str], fallback: str) -> str | None:
    if not names:
        return None
    return f"{label}: " + ", ".join(f"[[{note_name(name, fallback)}]]" for name in names)


def obsidian_note(notes: Artifact, info: TopicInfo, *, exported_on: date) -> str:
    """The Markdown file of the topic: YAML, the blocks, the links to the neighbour topics."""
    topic = str(info.topic_id)
    head = [
        "---",
        f"domain: {_yaml(info.domain_name or (str(info.domain_id) if info.domain_id else None))}",
        f"domain_id: {_yaml(str(info.domain_id) if info.domain_id else None)}",
        f"topic_id: {_yaml(topic)}",
        f"date: {_yaml(exported_on.isoformat())}",
        f"sources: {_yaml(_cited(notes.blocks))}",
        "---",
        "",
        f"# {info.name}",
    ]
    parts = ["\n".join(head)]
    for block in notes.blocks:
        if not block.content.strip():
            continue
        lines = [f"## {BLOCK_HEADINGS[block.kind]}", ""]
        if block.unverified:
            lines += ["> [!warning] не проверено", ""]
        lines.append(block.content.strip())
        parts.append("\n".join(lines))
    links = [
        line
        for line in (
            _links("Опирается на", info.builds_on, topic),
            _links("Нужна для", info.needed_for, topic),
        )
        if line
    ]
    if links:
        parts.append("\n".join(["## Связи тем", "", *links]))
    return "\n\n".join(parts) + "\n"


def obsidian_vault(notes: Artifact, info: TopicInfo, *, exported_on: date) -> bytes:
    """The zip of the vault: one file named after the topic."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            f"{note_name(info.name, str(info.topic_id))}.md",
            obsidian_note(notes, info, exported_on=exported_on),
        )
    return buffer.getvalue()


# --- Anki -------------------------------------------------------------------------------------


def _anki_id(name: str) -> int:
    """A stable id of a model or a deck: Anki merges by it, so a repeated export updates what
    the learner already imported."""
    return uuid5(QUACK_UUID_NS, name).int % (1 << 31)


def _anki_html(text: str) -> str:
    """Card text as HTML with the formulas in Anki's MathJax delimiters."""
    safe = escape(text, quote=False)
    safe = _DISPLAY_MATH.sub(lambda m: f"\\[{m.group(1)}\\]", safe)
    return _INLINE_MATH.sub(lambda m: f"\\({m.group(1)}\\)", safe)


def answer_text(spec: AnswerSpec) -> str:
    """The answer of a task as the back of a card; a form with no short answer gives empty."""
    if spec.options:
        return "; ".join(o.text for o in spec.options if o.correct)
    if spec.value is not None:
        return f"${spec.value.latex}$"
    if spec.items:
        return "; ".join(f"${item.latex}$" for item in spec.items)
    if spec.accepted:
        return spec.accepted[0]
    if spec.key_ideas:
        return "; ".join(spec.key_ideas)
    return ""


def anki_deck(
    info: TopicInfo, cards: Sequence[AnkiCard], *, timestamp: float | None = None
) -> bytes:
    """The `.apkg` of the topic's fixed cards; `timestamp` fixes the build for tests."""
    model = genanki.Model(
        _anki_id("anki-model:front-back"),
        "Quack card",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[
            {
                "name": "Card",
                "qfmt": "{{Front}}",
                "afmt": '{{FrontSide}}<hr id="answer">{{Back}}',
            }
        ],
    )
    deck = genanki.Deck(_anki_id(f"anki-deck:{info.topic_id}"), info.name)
    for card in cards:
        deck.add_note(
            genanki.Note(
                model=model,
                fields=[_anki_html(card.prompt), _anki_html(card.answer)],
                guid=genanki.guid_for(str(card.card_id)),
            )
        )
    buffer = io.BytesIO()
    genanki.Package(deck).write_to_file(buffer, timestamp=timestamp)
    return buffer.getvalue()


# --- reads ------------------------------------------------------------------------------------


async def load_topic_info(
    db: Db, reader: GraphReader, *, learner_id: UUID, topic_id: UUID, domain_id: UUID
) -> TopicInfo:
    """Name, domain and neighbours of the topic. The name is the graph's (merges and renames
    applied); a topic the projector has not reached yet keeps the name of its event."""
    found = await reader.topic_memory([topic_id])
    name = found[0].name if found else None
    if name is None:
        row = await db.fetch_one(
            _TOPIC_NAME,
            {
                "learner_id": learner_id,
                "types": ["topic.created", "topic.proposed"],
                "topic_id": str(topic_id),
            },
        )
        name = row["name"] if row else str(topic_id)
    domain = await db.fetch_one(
        _DOMAIN_NAME, {"learner_id": learner_id, "domain_id": str(domain_id)}
    )
    links = await reader.topic_requires(domain_id)
    needs = [link.to_topic_id for link in links if link.from_topic_id == topic_id]
    needed_by = [link.from_topic_id for link in links if link.to_topic_id == topic_id]
    names = (
        {t.topic_id: t.name for t in await reader.topic_memory([*needs, *needed_by])}
        if needs or needed_by
        else {}
    )
    return TopicInfo(
        topic_id=topic_id,
        name=name,
        domain_id=domain_id,
        domain_name=domain["name"] if domain else None,
        builds_on=[names[t] for t in needs if t in names],
        needed_for=[names[t] for t in needed_by if t in names],
    )


async def anki_cards(db: Db, *, learner_id: UUID, cards: Artifact) -> list[AnkiCard]:
    """The cards of the current version with the answers of their tasks (read for this learner
    only, Т-§3.5)."""
    out: list[AnkiCard] = []
    for card in cards.cards:
        task = await get_task(db, learner_id=learner_id, task_id=card.card_id)
        answer = answer_text(task.answer_spec) if task is not None else ""
        out.append(
            AnkiCard(card_id=card.card_id, kind=card.kind, prompt=card.prompt, answer=answer)
        )
    return out
