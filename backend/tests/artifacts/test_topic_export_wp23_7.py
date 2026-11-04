"""WP23.7: the export of a topic to Obsidian and Anki and the state of a data export (ТЗ Ф4 WP23.7,
§6.4 «Артефакты»; Т-§19.6, Т-§19.7; D-C-240): a zip vault with one file per topic, `[[links]]` by
the derived `REQUIRES`, YAML at the top and formulas in `$…$`; an Anki deck of the fixed cards;
`GET /v1/learner/exports/{id}` with a signed link of the storage."""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest

from quack.api.app import create_app
from quack.api.ratelimit import enforce_action_rate_limit
from quack.artifacts.cards import write_system_cards
from quack.artifacts.documents import Artifact, Block, SystemBlock, write_system_blocks
from quack.artifacts.export_routes import get_export_reader
from quack.artifacts.topic_export import (
    AnkiCard,
    TopicInfo,
    anki_deck,
    answer_text,
    obsidian_vault,
)
from quack.config import Settings
from quack.db import Db
from quack.events.log import NewEvent, append_events
from quack.events.values import AnswerOption, AnswerSpec, MathValue
from quack.graph.read.models import SkillFact, TopicMemory, TopicRequires
from tests.artifacts.test_cards_wp23 import _card  # pyright: ignore[reportPrivateUsage]
from tests.db.fixtures import create_learner
from tests.support.auth import session_cookie, session_token
from tests.support.graph_read import FixedGraphReader
from tests.support.resources import build_app_resources
from tests.support.storage import InMemoryObjectStore

DAY = date(2026, 11, 5)
DOMAIN = uuid4()
LIMITS, SERIES, DERIVATIVE = uuid4(), uuid4(), uuid4()
MATERIAL = uuid4()

type ClientFor = Callable[[UUID], Awaitable[httpx.AsyncClient]]


def _block(kind: str, content: str, **extra: object) -> Block:
    return Block.model_validate(
        {"block_id": uuid4(), "kind": kind, "content": content, "owner": "system", **extra}
    )


def _notes(blocks: Sequence[Block]) -> Artifact:
    return Artifact(
        artifact_id=uuid4(), kind="notes", domain_id=DOMAIN, topic_id=LIMITS, hub_id=None,
        version=2, blocks=tuple(blocks),
    )  # fmt: skip


def _info(**changes: object) -> TopicInfo:
    base = TopicInfo(
        topic_id=LIMITS, name="Limits", domain_id=DOMAIN, domain_name="Calculus",
        builds_on=["Series"], needed_for=["Derivative"],
    )  # fmt: skip
    return base.model_copy(update=changes)


def _vault(blocks: Sequence[Block], info: TopicInfo | None = None) -> zipfile.ZipFile:
    data = obsidian_vault(_notes(blocks), info or _info(), exported_on=DAY)
    return zipfile.ZipFile(io.BytesIO(data))


# --- Obsidian ---------------------------------------------------------------------------------


def test_vault_is_a_zip_with_one_file_per_topic() -> None:
    """Т-§19.7: «файл на тему», архив zip."""
    archive = _vault([_block("definition", "A limit is $L$.")])
    assert archive.namelist() == ["Limits.md"]


def test_vault_file_has_yaml_with_domain_topic_date_and_sources() -> None:
    """Т-§19.7: YAML в начале — домен, id темы, дата, источники (the chunks the blocks cite)."""
    cited = _block(
        "statement",
        "Every convergent sequence is bounded.",
        sources=[{"chunk_id": str(uuid4()), "material_id": str(MATERIAL), "page": 7}],
    )
    text = _vault([cited]).read("Limits.md").decode()
    head, _, body = text.removeprefix("---\n").partition("\n---\n")
    fields = dict(line.split(": ", 1) for line in head.splitlines())
    assert json.loads(fields["domain"]) == "Calculus"
    assert json.loads(fields["domain_id"]) == str(DOMAIN)
    assert json.loads(fields["topic_id"]) == str(LIMITS)
    assert json.loads(fields["date"]) == "2026-11-05"
    assert json.loads(fields["sources"]) == [{"material_id": str(MATERIAL), "page": 7}]
    assert body.lstrip().startswith("# Limits")


def test_vault_links_the_derived_requires_with_wikilinks() -> None:
    """Т-§19.7: связи тем — `[[внутренние ссылки]]` по выведенным `REQUIRES`."""
    text = _vault([_block("summary", "x")]).read("Limits.md").decode()
    assert "[[Series]]" in text and "[[Derivative]]" in text
    lone = _vault([_block("summary", "x")], _info(builds_on=[], needed_for=[]))
    assert "[[" not in lone.read("Limits.md").decode()


def test_vault_keeps_formulas_in_dollars_and_marks_unverified_blocks() -> None:
    text = _vault(
        [
            _block("definition", r"$\lim_{n\to\infty} a_n = L$", unverified=True),
            _block("custom", "My own words", owner="learner"),
        ]
    ).read("Limits.md").decode()  # fmt: skip
    assert r"$\lim_{n\to\infty} a_n = L$" in text
    assert "My own words" in text
    assert "не проверено" in text


def test_vault_file_name_is_safe_and_the_links_match_it() -> None:
    """The name of the file is the name of the topic without the characters Obsidian forbids; a
    link to a topic with such a name points at the same file name."""
    info = _info(name="Limits: a/b?", builds_on=["Series #1"], needed_for=[])
    archive = _vault([_block("summary", "x")], info)
    assert archive.namelist() == ["Limits a b.md"]
    assert "[[Series 1]]" in archive.read("Limits a b.md").decode()


# --- Anki -------------------------------------------------------------------------------------


def _collection(deck: bytes) -> list[tuple[str, str]]:
    """(front, back) of the notes of an `.apkg`."""
    with tempfile.TemporaryDirectory() as tmp:
        zipfile.ZipFile(io.BytesIO(deck)).extract("collection.anki2", tmp)
        connection = sqlite3.connect(Path(tmp) / "collection.anki2")
        try:
            rows = connection.execute("SELECT flds FROM notes ORDER BY id").fetchall()
        finally:
            connection.close()
    return [tuple(str(row[0]).split("\x1f"))[:2] for row in rows]  # pyright: ignore[reportReturnType]


def test_anki_deck_holds_the_fixed_cards_front_and_back() -> None:
    """Т-§19.7: колода Anki, только фиксированные карточки; формулы `$…$` — MathJax Anki."""
    cards = [
        AnkiCard(card_id=uuid4(), kind="definition", prompt="What is $\\lim a_n$?", answer="$L$"),
        AnkiCard(card_id=uuid4(), kind="distinction", prompt="Sum <b>vs</b> limit", answer="x"),
    ]
    notes = _collection(anki_deck(_info(), cards))
    assert len(notes) == 2
    fronts = {front for front, _ in notes}
    assert "What is \\(\\lim a_n\\)?" in fronts
    assert "Sum &lt;b&gt;vs&lt;/b&gt; limit" in fronts
    assert ("What is \\(\\lim a_n\\)?", "\\(L\\)") in notes


def test_anki_deck_is_the_same_for_the_same_cards() -> None:
    """A repeat of the export gives the same deck and the same notes, so Anki updates, not
    duplicates, what the learner has imported."""
    cards = [AnkiCard(card_id=uuid4(), kind="definition", prompt="p", answer="a")]

    def ids(deck: bytes) -> tuple[list[object], list[object]]:
        with tempfile.TemporaryDirectory() as tmp:
            zipfile.ZipFile(io.BytesIO(deck)).extract("collection.anki2", tmp)
            connection = sqlite3.connect(Path(tmp) / "collection.anki2")
            try:
                guids = [r[0] for r in connection.execute("SELECT guid FROM notes")]
                decks = [
                    sorted(json.loads(r[0])) for r in connection.execute("SELECT decks FROM col")
                ]
            finally:
                connection.close()
        return guids, decks

    assert ids(anki_deck(_info(), cards)) == ids(anki_deck(_info(), cards))


def test_answer_text_of_each_form() -> None:
    """The back of a card is the answer of its task."""
    number = AnswerSpec(type="number", value=MathValue(latex="\\frac{1}{3}", sympy="1/3"))
    assert answer_text(number) == "$\\frac{1}{3}$"
    choice = AnswerSpec(
        type="choice",
        options=[
            AnswerOption(option_id="A", text="bounded", correct=True),
            AnswerOption(option_id="B", text="monotone", correct=False),
        ],
    )
    assert answer_text(choice) == "bounded"
    short = AnswerSpec(type="short_text", accepted=["compact", "closed and bounded"])
    assert answer_text(short) == "compact"
    assert answer_text(AnswerSpec(type="proof", key_ideas=["induction", "bound"])) == (
        "induction; bound"
    )
    assert answer_text(AnswerSpec(type="drawing")) == ""


# --- routes -----------------------------------------------------------------------------------


class _Reader(FixedGraphReader):
    """«Limits» builds on «Series» and is needed for «Derivative»."""

    async def topic_memory(self, topic_ids: Sequence[UUID]) -> list[TopicMemory]:
        names = {LIMITS: "Limits", SERIES: "Series", DERIVATIVE: "Derivative"}
        return [
            TopicMemory(
                topic_id=t, name=names[t], domain_id=DOMAIN,
                skills=[SkillFact(skill_id=uuid4(), name="a skill")],
            )
            for t in topic_ids
            if t in names
        ]  # fmt: skip

    async def topic_requires(self, domain_id: UUID) -> list[TopicRequires]:
        return [
            TopicRequires(from_topic_id=LIMITS, to_topic_id=SERIES, weight=1),
            TopicRequires(from_topic_id=DERIVATIVE, to_topic_id=LIMITS, weight=2),
        ]


@pytest.fixture
def store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
async def client_for(
    settings: Settings, pg_db: Db, store: InMemoryObjectStore
) -> AsyncIterator[ClientFor]:
    opened: list[httpx.AsyncClient] = []

    async def make(learner_id: UUID) -> httpx.AsyncClient:
        app = create_app(settings)
        app.state.resources = build_app_resources(settings, None, None, db=pg_db, storage=store)
        app.dependency_overrides[enforce_action_rate_limit] = lambda: None
        app.dependency_overrides[get_export_reader] = lambda: _Reader()
        cookie = session_cookie(await session_token(pg_db, learner_id, settings))
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=cookie
        )
        opened.append(client)
        return client

    yield make
    for client in opened:
        await client.aclose()


async def _topic(db: Db, learner_id: UUID, topic_id: UUID = LIMITS, name: str = "Limits") -> None:
    events = [
        NewEvent(
            type="domain.created",
            payload={"domain_id": DOMAIN, "name": "Calculus"},
            actor="code",
            idempotency_key=f"export:domain:{DOMAIN}",
            domain_id=DOMAIN,
        ),
        NewEvent(
            type="topic.created",
            payload={"topic_id": topic_id, "domain_id": DOMAIN, "name": name, "origin": "session"},
            actor="code",
            idempotency_key=f"export:topic:{topic_id}",
            domain_id=DOMAIN,
        ),
    ]
    await append_events(db, learner_id, events)


async def test_obsidian_route_returns_the_vault_of_the_topic(
    pg_db: Db, client_for: ClientFor
) -> None:
    learner = await create_learner(pg_db)
    await _topic(pg_db, learner)
    await write_system_blocks(
        pg_db, learner_id=learner, kind="notes", owner_id=LIMITS,
        blocks=[SystemBlock(kind="definition", content="A limit is $L$.")],
        created_by="artifact_writer@1",
    )  # fmt: skip
    async with await client_for(learner) as client:
        response = await client.get(f"/v1/topics/{LIMITS}/export/obsidian")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    text = archive.read("Limits.md").decode()
    assert "A limit is $L$." in text
    assert "[[Series]]" in text and "[[Derivative]]" in text
    assert '"Calculus"' in text


async def test_obsidian_route_of_a_foreign_or_unknown_topic_is_404(
    pg_db: Db, client_for: ClientFor
) -> None:
    """T-E3: another learner's topic is not found, and nothing of the owner leaks."""
    owner, other = await create_learner(pg_db), await create_learner(pg_db)
    await _topic(pg_db, owner)
    async with await client_for(other) as client:
        assert (await client.get(f"/v1/topics/{LIMITS}/export/obsidian")).status_code == 404
        assert (await client.get(f"/v1/topics/{LIMITS}/export/anki")).status_code == 404
        assert (await client.get(f"/v1/topics/{uuid4()}/export/anki")).status_code == 404


async def test_anki_route_returns_the_deck_with_the_answers_of_the_tasks(
    pg_db: Db, client_for: ClientFor
) -> None:

    learner = await create_learner(pg_db)
    await _topic(pg_db, learner)
    card = await _card(pg_db, learner, "When does the ratio test apply?")
    settings = Settings()
    await write_system_cards(
        pg_db, settings, learner_id=learner, topic_id=LIMITS, cards=[card],
        created_by="artifact_writer@1",
    )  # fmt: skip
    async with await client_for(learner) as client:
        response = await client.get(f"/v1/topics/{LIMITS}/export/anki")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    notes = _collection(response.content)
    assert notes == [("When does the ratio test apply?", "\\(\\frac{1}{3}\\)")]


async def test_routes_need_a_signed_in_learner(settings: Settings) -> None:
    app = create_app(settings)
    app.state.resources = build_app_resources(settings, None, None)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in (
            f"/v1/topics/{LIMITS}/export/obsidian",
            f"/v1/topics/{LIMITS}/export/anki",
            f"/v1/learner/exports/{uuid4()}",
        ):
            assert (await client.get(path)).status_code == 401


# --- GET /v1/learner/exports/{id} -------------------------------------------------------------


async def _export_row(
    db: Db, learner_id: UUID, status: str, key: str | None, expires_at: datetime | None
) -> UUID:
    export_id = uuid4()
    await db.execute(
        "INSERT INTO content.exports (export_id, learner_id, status, object_key, expires_at) "
        "VALUES (%(e)s, %(l)s, %(s)s, %(k)s, %(x)s)",
        {"e": export_id, "l": learner_id, "s": status, "k": key, "x": expires_at},
    )
    return export_id


async def test_export_status_ready_gives_a_signed_link_for_the_rest_of_the_term(
    pg_db: Db, client_for: ClientFor, store: InMemoryObjectStore
) -> None:
    learner = await create_learner(pg_db)
    key = f"learners/{learner}/exports/e-1.zip"
    await store.put(key, b"PK", "application/zip")
    expires = datetime.now(UTC) + timedelta(hours=2)
    export_id = await _export_row(pg_db, learner, "ready", key, expires)
    async with await client_for(learner) as client:
        response = await client.get(f"/v1/learner/exports/{export_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["export_id"] == str(export_id) and body["status"] == "ready"
    assert body["download_url"].startswith(f"memory://quack-learners/{key}?ttl=")
    assert 7000 <= body["expires_in_s"] <= 7200
    assert body["download_url"].endswith(f"ttl={body['expires_in_s']}")


async def test_export_status_after_the_term_has_no_link(
    pg_db: Db, client_for: ClientFor, store: InMemoryObjectStore
) -> None:
    """A ready archive past `expires_at` is `expired` for the learner even before
    `exports.cleanup` has run; a cleaned one is `expired` with no key."""
    learner = await create_learner(pg_db)
    key = f"learners/{learner}/exports/e-2.zip"
    await store.put(key, b"PK", "application/zip")
    late = await _export_row(pg_db, learner, "ready", key, datetime.now(UTC) - timedelta(hours=1))
    cleaned = await _export_row(pg_db, learner, "expired", None, datetime.now(UTC))
    async with await client_for(learner) as client:
        for export_id in (late, cleaned):
            body = (await client.get(f"/v1/learner/exports/{export_id}")).json()
            assert body["status"] == "expired"
            assert body["download_url"] is None and body["expires_in_s"] is None


async def test_export_status_pending_while_the_request_has_no_row(
    pg_db: Db, client_for: ClientFor
) -> None:
    """The job writes the row when the archive is ready; until then the id of the request in
    `agent.requested {kind: data_export}` is `pending`. A request of another kind or learner, or
    no request at all, is 404."""
    learner, other = await create_learner(pg_db), await create_learner(pg_db)
    pending, viz = uuid4(), uuid4()
    for learner_id, request_id, kind in ((learner, pending, "data_export"), (learner, viz, "viz")):
        await pg_db.execute(
            "INSERT INTO core.events (learner_id, seq, type, schema_version, actor, "
            "idempotency_key, payload_hash, occurred_at, payload) VALUES (%(l)s, "
            "(SELECT COALESCE(MAX(seq), 0) + 1 FROM core.events WHERE learner_id = %(l)s), "
            "'agent.requested', 1, 'learner', %(k)s, 'h', now(), %(p)s::jsonb)",
            {
                "l": learner_id,
                "k": f"agent.requested:{request_id}",
                "p": json.dumps({"request_id": str(request_id), "kind": kind}),
            },
        )
    async with await client_for(learner) as client:
        body = (await client.get(f"/v1/learner/exports/{pending}")).json()
        assert body["status"] == "pending" and body["download_url"] is None
        assert (await client.get(f"/v1/learner/exports/{viz}")).status_code == 404
        assert (await client.get(f"/v1/learner/exports/{uuid4()}")).status_code == 404
    async with await client_for(other) as client:
        assert (await client.get(f"/v1/learner/exports/{pending}")).status_code == 404


async def test_export_status_of_another_learner_is_404(
    pg_db: Db, client_for: ClientFor, store: InMemoryObjectStore
) -> None:
    owner, other = await create_learner(pg_db), await create_learner(pg_db)
    key = f"learners/{owner}/exports/e-3.zip"
    await store.put(key, b"PK", "application/zip")
    export_id = await _export_row(
        pg_db, owner, "ready", key, datetime.now(UTC) + timedelta(hours=1)
    )
    async with await client_for(other) as client:
        assert (await client.get(f"/v1/learner/exports/{export_id}")).status_code == 404
