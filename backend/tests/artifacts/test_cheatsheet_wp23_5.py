"""WP23.5: the cheat sheet of a hub — one A4 page, «собери сам» review, consent
(ТЗ Ф4 WP23.5; Т-§17.6, Т-§17.7; D-C-143, D-C-144; T-AR4)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.artifacts.documents import ArtifactNotFound, Block, edit_block, get_artifact
from quack.artifacts.hub_cheatsheet import (
    cheatsheet_html,
    consent_event,
    generate_hub_cheatsheet,
    hub_writer_input,
    request_hub_cheatsheet,
    review_hub_cheatsheet,
)
from quack.compute_client.contracts import RenderPdfResponse
from quack.config import Settings
from quack.db import Db
from quack.graph.read import HubGoal, HubTopicGoal, TopicMemory
from quack.graph.read.materials import ChunkHit
from quack.graph.read.models import MisconceptionFact, SkillFact
from quack.jobs.runner import JOB_HANDLERS
from quack.jobs.types import ClaimedJob
from tests.artifacts.hubs import create_hub
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.graph_read import FixedGraphReader
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources, build_job_context

LIMIT, CHAIN, MISCONCEPTION, CHUNK = uuid4(), uuid4(), uuid4(), uuid4()
TOPIC = uuid4()
HIT = ChunkHit(
    chunk_id=CHUNK, material_id=uuid4(), page=3, text="A limit is…", teaches_focus=True, score=None
)
PDF = "/v1/render/pdf"


class HubReader(FixedGraphReader):
    """The hub «Calculus exam» with one topic «Limits» of two skills."""

    def __init__(self, hub_id: UUID) -> None:
        super().__init__()
        self.hub_id = hub_id

    async def domain_goals(self, domain_id: UUID) -> list[HubGoal]:
        topic = HubTopicGoal(topic_id=TOPIC, name="Limits", required_levels=[3])
        return [
            HubGoal(
                hub_id=self.hub_id, name="Calculus exam", kind="request", control_type="tasks",
                topics=[topic],
            )
        ]  # fmt: skip

    async def topic_memory(self, topic_ids: Sequence[UUID]) -> list[TopicMemory]:
        if TOPIC not in topic_ids:
            return []
        return [
            TopicMemory(
                topic_id=TOPIC,
                name="Limits",
                domain_id=uuid4(),
                skills=[
                    SkillFact(skill_id=LIMIT, name="Limit of a sequence"),
                    SkillFact(skill_id=CHAIN, name="Chain rule"),
                ],
                misconceptions=[
                    MisconceptionFact(
                        misconception_id=MISCONCEPTION, skill_id=LIMIT, status="suspected",
                        statement="the limit is the last term", quotes=["lim = a_100"],
                    )
                ],
            )
        ]  # fmt: skip


async def prefetch(skill_ids: Sequence[UUID]) -> list[ChunkHit]:
    return [HIT] if LIMIT in skill_ids else []


def _block(content: str, **extra: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "updates": None, "kind": "summary", "content": content, "sources": [], "skills": [],
    }  # fmt: skip
    return block | extra


class Writer:
    """`artifact_writer@1` that answers a different one-block sheet on every call."""

    def __init__(self, provider: ScriptedLLMProvider) -> None:
        self.prompts: list[str] = []
        provider.respond("ArtifactWriterOutput", self)

    def __call__(self, request: Any) -> dict[str, Any]:
        self.prompts.append(request.prompt)
        return {"blocks": [_block(f"Formulas, draft {len(self.prompts)}: $x^2$")], "cards": []}


def _pages(*counts: int) -> list[RenderPdfResponse]:
    return [RenderPdfResponse(pdf_b64="", page_count=n) for n in counts]


class Stand:
    def __init__(self, settings: Settings, pg: Db) -> None:
        self.settings = settings
        self.provider = ScriptedLLMProvider()
        self.writer = Writer(self.provider)
        self.scenario = ComputeScenario()
        self.resources = build_app_resources(
            settings, None, None, db=pg, provider=self.provider,
            compute=scripted_compute(self.scenario),
        )  # fmt: skip

    def renders(self) -> list[str]:
        return [r.content.decode() for r in self.scenario.requests if r.url.path == PDF]

    async def generate(
        self, learner: UUID, hub: UUID, *, key: str = "request-1", settings: Settings | None = None
    ) -> Any:
        r = self.resources
        return await generate_hub_cheatsheet(
            r.db, r.llm, r.compute, HubReader(hub), prefetch, settings or self.settings,
            learner_id=learner, hub_id=hub, key=key,
        )  # fmt: skip


@pytest.fixture
def stand(settings: Settings, pg_db: Db) -> Stand:
    return Stand(settings, pg_db)


# --- T-AR4: one page --------------------------------------------------------------------


async def test_cheatsheet_one_page(stand: Stand, pg_db: Db) -> None:
    """T-AR4: a PDF of more than one page → the writer is asked again to shorten it, up to
    `cheatsheet_fit_rounds`; the page count comes from compute `/v1/render/pdf` (A4)."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(PDF, *_pages(3, 2, 1))
    result = await stand.generate(learner, hub)
    assert (result.fits, result.page_count, result.rounds, result.version) == (True, 1, 2, 3)
    assert len(stand.writer.prompts) == 3 and len(stand.renders()) == 3
    assert "The last version took" not in stand.writer.prompts[0]
    assert (
        "took 3 pages" in stand.writer.prompts[1] and "fit on one page" in stand.writer.prompts[1]
    )
    assert "took 2 pages" in stand.writer.prompts[2]
    sheet = await get_artifact(pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub)
    assert [b.content for b in sheet.blocks] == ["Formulas, draft 3"]


async def test_cheatsheet_stops_after_fit_rounds(stand: Stand, pg_db: Db) -> None:
    """The sheet that still does not fit stays as the last version; no more than
    `cheatsheet_fit_rounds` repeats, and 0 means no repeat at all."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(PDF, *_pages(4, 3, 2))
    result = await stand.generate(learner, hub)
    assert (result.fits, result.page_count, result.rounds, result.version) == (False, 2, 2, 3)
    assert len(stand.writer.prompts) == 1 + stand.settings.cheatsheet_fit_rounds

    stand.scenario.push_for(PDF, *_pages(2))
    once = stand.settings.model_copy(update={"cheatsheet_fit_rounds": 0})
    other, _ = await create_hub(pg_db, learner)
    result = await stand.generate(learner, other, settings=once)
    assert (result.fits, result.rounds) == (False, 0)
    assert len(stand.writer.prompts) == 4


async def test_a_sheet_that_fits_is_asked_once(stand: Stand, pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(PDF, *_pages(1))
    result = await stand.generate(learner, hub)
    assert (result.fits, result.rounds, result.version) == (True, 0, 1)
    assert len(stand.writer.prompts) == 1


async def test_a_repeated_job_writes_no_new_version(stand: Stand, pg_db: Db) -> None:
    """Law 5: the same request key does not ask the model again (D-C-217: the version it wrote
    is found by the key of its `artifact.updated` event)."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(PDF, *_pages(1, 1))
    first = await stand.generate(learner, hub, key="request-7")
    again = await stand.generate(learner, hub, key="request-7")
    assert (first.version, again.version) == (1, 1)
    assert len(stand.writer.prompts) == 1


async def test_a_repeat_after_an_interrupted_fit_goes_on_from_the_next_round(
    stand: Stand, pg_db: Db
) -> None:
    """The job died after round 0: the repeat renders what is written, asks for the shortening
    only (round 1), and the earlier round is not asked again."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(PDF, *_pages(3))
    once = stand.settings.model_copy(update={"cheatsheet_fit_rounds": 0})
    cut = await stand.generate(learner, hub, key="request-9", settings=once)
    assert (cut.version, cut.fits) == (1, False) and len(stand.writer.prompts) == 1

    stand.scenario.push_for(PDF, *_pages(3, 1))
    done = await stand.generate(learner, hub, key="request-9")
    assert (done.version, done.page_count, done.rounds, done.fits) == (2, 1, 1, True)
    assert len(stand.writer.prompts) == 2
    assert "The last version took 3 pages" in stand.writer.prompts[1]


async def test_a_new_request_key_asks_again(stand: Stand, pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(PDF, *_pages(1, 1))
    await stand.generate(learner, hub, key="request-1")
    again = await stand.generate(learner, hub, key="request-2")
    assert again.version == 2 and len(stand.writer.prompts) == 2


async def test_the_page_count_is_of_the_whole_document(stand: Stand, pg_db: Db) -> None:
    """What is rendered is what the learner holds: their own blocks go to the PDF too."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    mine = await get_artifact(pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub)
    await edit_block(
        pg_db, learner_id=learner, artifact_id=mine.artifact_id, block_id=uuid4(),
        content="My own trap: forgot g'", idempotency_key="mine",
    )  # fmt: skip
    stand.scenario.push_for(PDF, *_pages(1))
    await stand.generate(learner, hub)
    (sent,) = stand.renders()
    assert "My own trap" in sent and "Formulas, draft 1" in sent


async def test_every_fit_round_sends_the_formulas_typeset_not_as_tex(
    stand: Stand, pg_db: Db
) -> None:
    """D-C-250: the page count that decides the next round is the one of the typeset page, so
    each rendered document carries its formula as an element for KaTeX, never as `$…$` text."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(PDF, *_pages(3, 2, 1))
    await stand.generate(learner, hub)
    sent = stand.renders()
    assert len(sent) == 3
    for document in sent:
        assert '<span class="quack-math" data-display="false">x^2</span>' in document
        assert "$x^2$" not in document


async def test_a_foreign_hub_is_not_found(stand: Stand, pg_db: Db) -> None:
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    with pytest.raises(ArtifactNotFound):
        await stand.generate(stranger, hub)
    assert stand.writer.prompts == [] and stand.renders() == []


# --- the HTML of the sheet ----------------------------------------------------------------


def test_cheatsheet_html_is_one_a4_document() -> None:
    def block(content: str, owner: str) -> Block:
        return Block.model_validate(
            {"block_id": uuid4(), "kind": "summary", "content": content, "owner": owner}
        )

    system = block(
        "Use **Lopital** when $0/0$.\n\n- sign: indeterminate\n- <script>x</script>", "system"
    )
    html = cheatsheet_html("Calculus <exam>", (system, block("My trap", "learner")))
    assert "Calculus &lt;exam&gt;" in html and "Calculus <exam>" not in html
    assert "@page" in html and "A4" in html
    assert "<strong>Lopital</strong>" in html and "<li>sign: indeterminate</li>" in html
    assert "$0/0$" not in html  # a formula is a typeset element, not TeX text (WP23.5, D-C-250)
    assert '<span class="quack-math" data-display="false">0/0</span>' in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert html.index("Lopital") < html.index("My trap")
    assert 'data-owner="learner"' in html


def _html_of(content: str) -> str:
    system = Block.model_validate(
        {"block_id": uuid4(), "kind": "summary", "content": content, "owner": "system"}
    )
    return cheatsheet_html("Hub", (system,))


def test_formulas_are_marked_for_the_typesetter_not_left_as_tex() -> None:
    """D-C-250: `$…$` is an inline formula, `$$…$$` a display one (also over several lines); the
    TeX source sits escaped inside the element and no markdown form touches it."""
    html = _html_of(
        "Slope $f'(x)=\\frac{a}{b}$ and $a**b*c_1$.\n\n$$\\int_0^1 x^2\\,dx\n= \\tfrac13$$"
    )
    assert """<span class="quack-math" data-display="false">f'(x)=\\frac{a}{b}</span>""" in html
    assert '<span class="quack-math" data-display="false">a**b*c_1</span>' in html
    assert (
        '<span class="quack-math" data-display="true">\\int_0^1 x^2\\,dx\n= \\tfrac13</span>'
        in html
    )
    assert "$" not in html and "<strong>" not in html and "<em>" not in html


def test_a_formula_is_escaped_and_money_is_not_a_formula() -> None:
    html = _html_of(r"Take $a<b$. It costs 5$ or 6$, and \$7 stays.")
    assert '<span class="quack-math" data-display="false">a&lt;b</span>' in html
    assert "5$ or 6$" in html and "and $7 stays" in html and "\\$7" not in html
    assert html.count('class="quack-math"') == 1


# --- the input of the writer --------------------------------------------------------------


async def test_hub_input_collects_the_topics_of_the_hub(pg_db: Db, settings: Settings) -> None:
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    context = await hub_writer_input(
        pg_db, HubReader(hub), prefetch, settings, learner_id=learner, hub_id=hub
    )
    assert context is not None
    v = context.variables
    assert v["artifact_type"].startswith("cheatsheet") and "review" not in v["artifact_type"]
    assert "Hub: Calculus exam" in v["topic_graph"] and "Topic: Limits" in v["topic_graph"]
    assert "S1: Limit of a sequence" in v["topic_graph"] and "S2: Chain rule" in v["topic_graph"]
    assert v["material_chunks"].startswith("P1 (page 3):")
    assert "Misconception M1: the limit is the last term" in v["personal"]
    assert v["current_blocks"] == "None yet."
    review = await hub_writer_input(
        pg_db, HubReader(hub), prefetch, settings, learner_id=learner, hub_id=hub, review=True
    )
    assert review is not None and review.variables["artifact_type"].startswith("cheatsheet review")
    unknown = await hub_writer_input(
        pg_db, HubReader(uuid4()), prefetch, settings, learner_id=learner, hub_id=hub
    )
    assert unknown is None  # the graph does not know the hub yet


# --- «собери сам — я проверю» -------------------------------------------------------------


async def test_review_adds_remarks_next_to_the_learners_blocks(stand: Stand, pg_db: Db) -> None:
    """The learner's block stays as it is; the remark is a supplement next to it (П-11.1)."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    mine = await get_artifact(pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub)
    block_id = uuid4()
    await edit_block(
        pg_db, learner_id=learner, artifact_id=mine.artifact_id, block_id=block_id,
        content="Chain rule: (f(g))' = f'(g)", idempotency_key="mine",
    )  # fmt: skip
    remark = _block("You lost the inner derivative g'.", updates=1, kind="my_traps", sources=["P1"])
    stand.provider.respond("ArtifactWriterOutput", lambda _r: {"blocks": [remark], "cards": []})
    r = stand.resources
    review = await review_hub_cheatsheet(
        r.db, r.llm, HubReader(hub), prefetch, stand.settings,
        learner_id=learner, hub_id=hub, key="review-1",
    )  # fmt: skip
    (note,) = review.remarks
    assert note.supplement_to == block_id and note.owner == "system"
    assert note.content == "You lost the inner derivative g'."
    own = next(b for b in review.artifact.blocks if b.block_id == block_id)
    assert own.owner == "learner" and own.content == "Chain rule: (f(g))' = f'(g)"
    (call,) = stand.provider.calls
    assert "cheatsheet review" in call.prompt and "Chain rule: (f(g))'" in call.prompt


async def test_review_of_an_empty_sheet_asks_nobody(stand: Stand, pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    r = stand.resources
    review = await review_hub_cheatsheet(
        r.db, r.llm, HubReader(hub), prefetch, stand.settings,
        learner_id=learner, hub_id=hub, key="review-1",
    )  # fmt: skip
    assert review.remarks == () and stand.provider.calls == []


# --- consent (D-C-143, D-C-144) -----------------------------------------------------------


def test_consent_event_names_the_artifact_and_the_hub() -> None:
    """The kind and the hub are parameters, never words (law 2, D-C-143)."""
    hub, request = uuid4(), uuid4()
    event = consent_event(hub, request, idempotency_key="k1")
    assert event.type == "agent.requested" and event.actor == "learner"
    assert event.payload == {
        "request_id": request,
        "kind": "artifact",
        "params": {"artifact": "cheatsheet", "hub_id": hub},
        "needed_in_session": False,
    }
    assert consent_event(hub, request, idempotency_key="k1").idempotency_key == (
        event.idempotency_key
    )


async def test_consent_of_a_foreign_hub_is_not_found(pg_db: Db) -> None:
    learner, stranger = await create_learner(pg_db), await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    with pytest.raises(ArtifactNotFound):
        await request_hub_cheatsheet(pg_db, stranger, hub, idempotency_key="k1")


async def test_consent_is_written_once_per_key(pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    first = await request_hub_cheatsheet(pg_db, learner, hub, idempotency_key="k1")
    again = await request_hub_cheatsheet(pg_db, learner, hub, idempotency_key="k1")
    assert first == again
    rows = await pg_db.fetch_all(
        "SELECT payload FROM core.events WHERE learner_id = %(learner_id)s "
        "AND type = 'agent.requested'",
        {"learner_id": learner},
    )
    assert len(rows) == 1
    params = {k: v for k, v in rows[0]["payload"]["params"].items() if v is not None}
    assert params == {"artifact": "cheatsheet", "hub_id": str(hub)}


# --- the job -------------------------------------------------------------------------------


def test_job_is_registered() -> None:
    assert JOB_HANDLERS["artifact.cheatsheet"].__module__ == "quack.artifacts.hub_cheatsheet"


async def test_a_job_without_consent_generates_nothing(settings: Settings, pg_db: Db) -> None:
    """D-C-144: the offer is a state; `hub.deadline_set` alone (a job without `request_id`)
    must not write a cheat sheet nobody agreed to."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    provider = ScriptedLLMProvider()
    writer = Writer(provider)
    scenario = ComputeScenario()
    ctx = build_job_context(
        settings, None, None, db=pg_db, provider=provider, compute=scripted_compute(scenario)
    )
    job = ClaimedJob(
        id=uuid4(), kind="artifact.cheatsheet", learner_id=learner, lock_key=f"artifact:{hub}",
        coalesce_key=f"artifact:{hub}", priority=2, payload={"hub_id": str(hub)}, attempt=1,
        max_attempts=5,
    )  # fmt: skip
    await JOB_HANDLERS["artifact.cheatsheet"](job, ctx)
    assert writer.prompts == [] and scenario.requests == []
