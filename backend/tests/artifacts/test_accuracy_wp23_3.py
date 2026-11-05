"""WP23.3: accuracy of generated artifacts — page references, formulas through compute, the mark
«не проверено», the mark «без опоры» (ТЗ Ф4 WP23.3, WP23.2; Т-§17.3, Т-§17.4; D-C-155–D-C-158;
T-AR2). The writer is `ScriptedLLMProvider`, compute is `ComputeScenario` on `MockTransport`."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID, uuid4

import pytest

from quack.artifacts.documents import edit_block, get_artifact
from quack.artifacts.hub_cheatsheet import generate_hub_cheatsheet, review_hub_cheatsheet
from quack.artifacts.topic_refresh import refresh_topic, refresh_topic_notes
from quack.compute_client.contracts import RenderPdfResponse
from quack.compute_client.math_contracts import MathCheckResponse
from quack.config import Settings
from quack.db import Db
from tests.artifacts.hubs import create_hub
from tests.artifacts.test_cheatsheet_wp23_5 import HIT as HUB_HIT
from tests.artifacts.test_cheatsheet_wp23_5 import HubReader
from tests.artifacts.test_cheatsheet_wp23_5 import prefetch as hub_prefetch
from tests.artifacts.test_topic_refresh_wp23 import CHUNK, HIT, TopicReader, _prefetch, _topic
from tests.db.fixtures import create_learner
from tests.support.compute import ComputeScenario, scripted_compute
from tests.support.llm import ScriptedLLMProvider
from tests.support.resources import build_app_resources

MATH = "/v1/math/check"
PDF = "/v1/render/pdf"
IDENTITY = r"\sin^{2} x + \cos^{2} x = 1"


def block(content: str, **extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "updates": None, "kind": "summary", "content": content, "sources": [], "skills": [],
        "formulas": [],
    }  # fmt: skip
    return base | extra


def verdict(outcome: str) -> MathCheckResponse:
    return MathCheckResponse(status="completed", outcome=outcome)  # type: ignore[arg-type]


class Stand:
    """The writer answers `answers[i]` on the i-th call (the last one repeats)."""

    def __init__(self, settings: Settings, pg: Db) -> None:
        self.settings = settings
        self.provider = ScriptedLLMProvider()
        self.scenario = ComputeScenario()
        self.answers: list[list[dict[str, Any]]] = []
        self.provider.respond("ArtifactWriterOutput", self._answer)
        self.resources = build_app_resources(
            settings, None, None, db=pg, provider=self.provider,
            compute=scripted_compute(self.scenario),
        )  # fmt: skip

    def writes(self, *answers: list[dict[str, Any]]) -> None:
        self.answers = list(answers)

    def _answer(self, _request: Any) -> dict[str, Any]:
        call = len(self.provider.calls_for(schema_name="ArtifactWriterOutput"))
        blocks = self.answers[min(call, len(self.answers)) - 1]
        return {"blocks": blocks, "cards": []}

    @property
    def writer_calls(self) -> int:
        return len(self.provider.calls_for(schema_name="ArtifactWriterOutput"))

    def math_requests(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.scenario.requests if r.url.path == MATH]

    async def refresh(self, learner: UUID, topic_id: UUID, *, key: str = "job-1") -> int | None:
        r = self.resources
        return await refresh_topic_notes(
            r.db, r.llm, TopicReader(topic_id, 1), _prefetch, self.settings,
            learner_id=learner, topic_id=topic_id, key=key, compute=r.compute,
        )  # fmt: skip


@pytest.fixture
def stand(settings: Settings, pg_db: Db) -> Stand:
    return Stand(settings, pg_db)


async def _notes(pg_db: Db, learner: UUID, topic_id: UUID) -> Any:
    return (await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)).blocks


# --- sources: the page of the chunk, definitions without a source (T-AR2) ----------------


async def test_a_definition_names_the_page_of_its_chunk(stand: Stand, pg_db: Db) -> None:
    """Т-§17.4: a definition from the materials is tied to the page (slide) of the chunk the
    writer named; code puts the page there, the writer cannot invent one."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.writes([block("A limit…", kind="definition", sources=["P1"], skills=["S1"])])
    assert await stand.refresh(learner, topic_id) == 1
    (b,) = await _notes(pg_db, learner, topic_id)
    assert b.sources == [{"chunk_id": str(CHUNK), "material_id": str(HIT.material_id), "page": 3}]
    assert b.basis == "materials" and b.unverified is False


async def test_output_checks_regenerate_once(stand: Stand, pg_db: Db) -> None:
    """T-AR2: a definition or a statement without a chunk, while the materials have chunks, is a
    violation — one more generation, then the element is dropped; the rest is written."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    bare = block("A limit is a number.", kind="definition", skills=["S1"])
    claim = block("The limit is unique.", kind="statement", skills=["S1"])
    gist = block("Limits describe where terms go.")
    stand.writes([bare, claim, gist])
    assert await stand.refresh(learner, topic_id) == 1
    assert stand.writer_calls == 2
    assert [b.content for b in await _notes(pg_db, learner, topic_id)] == [gist["content"]]


async def test_a_fixed_definition_on_the_second_try_is_kept(stand: Stand, pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    bare = block("A limit is a number.", kind="definition", skills=["S1"])
    cited = block("A limit is a number.", kind="definition", sources=["P1"], skills=["S1"])
    stand.writes([bare], [cited])
    assert await stand.refresh(learner, topic_id) == 1
    assert stand.writer_calls == 2
    (b,) = await _notes(pg_db, learner, topic_id)
    assert (b.kind, b.basis) == ("definition", "materials")


# --- «без опоры»: an explanation of the system ----------------------------------------------


async def test_a_block_without_a_source_is_a_system_explanation(stand: Stand, pg_db: Db) -> None:
    """Т-§17.4: what does not rest on the materials is marked as an explanation of the system,
    not a quote of the course; no violation, no second generation."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.writes([block("Why it works…", kind="why"), block("Gist", sources=["P1"])])
    assert await stand.refresh(learner, topic_id) == 1
    assert stand.writer_calls == 1
    why, gist = await _notes(pg_db, learner, topic_id)
    assert (why.basis, gist.basis) == ("system", "materials")


async def test_without_materials_a_definition_is_a_system_explanation(
    stand: Stand, pg_db: Db
) -> None:
    """No chunk in the context — nothing to cite: the definition stays, marked «без опоры»."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)

    async def no_chunks(_skills: Any) -> list[Any]:
        return []

    stand.writes([block("A limit is a number.", kind="definition", skills=["S1"])])
    r = stand.resources
    version = await refresh_topic_notes(
        r.db, r.llm, TopicReader(topic_id, 1), no_chunks, stand.settings,
        learner_id=learner, topic_id=topic_id, key="job-1", compute=r.compute,
    )  # fmt: skip
    assert version == 1 and stand.writer_calls == 1
    (b,) = await _notes(pg_db, learner, topic_id)
    assert (b.kind, b.basis) == ("definition", "system")


async def test_a_learner_edit_clears_the_marks(stand: Stand, pg_db: Db) -> None:
    """A block the learner changed is theirs: neither «без опоры» nor «не проверено» is about it."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.scenario.push_for(MATH, verdict("incorrect"), verdict("incorrect"))
    stand.writes([block("Identity", formulas=[IDENTITY])])
    await stand.refresh(learner, topic_id)
    notes = await get_artifact(pg_db, learner_id=learner, kind="notes", owner_id=topic_id)
    (system,) = notes.blocks
    assert (system.basis, system.unverified) == ("system", True)
    await edit_block(
        pg_db, learner_id=learner, artifact_id=notes.artifact_id, block_id=system.block_id,
        content="my version", idempotency_key="edit-1",
    )  # fmt: skip
    (mine,) = await _notes(pg_db, learner, topic_id)
    assert (mine.owner, mine.basis, mine.unverified) == ("learner", None, False)


# --- formulas through compute -----------------------------------------------------------------


async def test_a_checked_formula_needs_no_mark(stand: Stand, pg_db: Db) -> None:
    """Т-§17.4: the identity goes to compute `/v1/math/check` (mode `equal`, both sides as LaTeX,
    the budget of a background check); it passed — the block is written as is."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.scenario.push_for(MATH, verdict("correct"))
    stand.writes([block("Pythagoras", formulas=[IDENTITY])])
    assert await stand.refresh(learner, topic_id) == 1
    assert stand.writer_calls == 1
    (request,) = stand.math_requests()
    assert request["kind"] == "expression" and request["mode"] == "equal"
    assert request["student"] == {"latex": r"\sin^{2} x + \cos^{2} x", "sympy": None}
    assert request["reference"] == {"latex": "1", "sympy": None}
    assert request["timeout_ms"] == stand.settings.symbolic_background_timeout_ms
    assert request["numeric"]["numeric_points"] == stand.settings.numeric_points
    (b,) = await _notes(pg_db, learner, topic_id)
    assert b.unverified is False


async def test_a_wrong_formula_is_regenerated_once(stand: Stand, pg_db: Db) -> None:
    """Not passed → one more generation; the corrected formula passes → no mark."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.scenario.push_for(MATH, verdict("incorrect"), verdict("correct"))
    stand.writes(
        [block("Pythagoras", formulas=[r"\sin x + \cos x = 1"])],
        [block("Pythagoras", formulas=[IDENTITY])],
    )
    assert await stand.refresh(learner, topic_id) == 1
    assert stand.writer_calls == 2 and len(stand.math_requests()) == 2
    (b,) = await _notes(pg_db, learner, topic_id)
    assert b.unverified is False


async def test_a_formula_that_fails_twice_is_marked_unverified(stand: Stand, pg_db: Db) -> None:
    """Failed again → the block stays with the mark «не проверено» (not dropped); the other
    blocks are written clean."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.scenario.push_for(MATH, verdict("incorrect"), verdict("incorrect"))
    wrong = block("Pythagoras", formulas=[r"\sin x + \cos x = 1"])
    stand.writes([wrong, block("Gist", sources=["P1"])])
    assert await stand.refresh(learner, topic_id) == 1
    assert stand.writer_calls == 2
    first, second = await _notes(pg_db, learner, topic_id)
    assert (first.unverified, second.unverified) == (True, False)
    assert first.content == "Pythagoras"


async def test_formula_the_compute_cannot_read_is_not_passed(stand: Stand, pg_db: Db) -> None:
    """`unclear` or a failed check is not a pass: same road as a wrong formula."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    failed = MathCheckResponse(status="failed", reason="unparsed")
    stand.scenario.push_for(MATH, failed, verdict("unclear"))
    stand.writes([block("Odd", formulas=[r"\oint f = 0"])])
    await stand.refresh(learner, topic_id)
    assert stand.writer_calls == 2
    (b,) = await _notes(pg_db, learner, topic_id)
    assert b.unverified is True


async def test_compute_down_marks_unverified_without_a_second_generation(
    stand: Stand, pg_db: Db
) -> None:
    """The writer is not at fault when compute is down: no regeneration, the block says «не
    проверено» and the notes are still written."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.scenario.push_for(MATH, 503)
    stand.writes([block("Pythagoras", formulas=[IDENTITY])])
    assert await stand.refresh(learner, topic_id) == 1
    assert stand.writer_calls == 1
    (b,) = await _notes(pg_db, learner, topic_id)
    assert b.unverified is True


async def test_without_compute_the_formula_is_unverified(stand: Stand, pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.writes([block("Pythagoras", formulas=[IDENTITY])])
    r = stand.resources
    await refresh_topic_notes(
        r.db, r.llm, TopicReader(topic_id, 1), _prefetch, stand.settings,
        learner_id=learner, topic_id=topic_id, key="job-1",
    )  # fmt: skip
    assert stand.writer_calls == 1 and stand.math_requests() == []
    (b,) = await _notes(pg_db, learner, topic_id)
    assert b.unverified is True


async def test_a_formula_that_is_not_an_identity_is_not_sent(stand: Stand, pg_db: Db) -> None:
    """Only `left = right` is an identity for `/v1/math/check`: an inequality or two signs never
    reach compute and count as not passed."""
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.writes([block("Bounds", formulas=[r"x^{2} \leq 4", "a = b = c"])])
    await stand.refresh(learner, topic_id)
    assert stand.writer_calls == 2 and stand.math_requests() == []
    (b,) = await _notes(pg_db, learner, topic_id)
    assert b.unverified is True


async def test_a_block_without_formulas_calls_nobody(stand: Stand, pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.writes([block("Gist")])
    await stand.refresh(learner, topic_id)
    assert stand.math_requests() == []


async def test_the_topic_job_passes_compute_to_the_notes(stand: Stand, pg_db: Db) -> None:
    learner = await create_learner(pg_db)
    topic_id = await _topic(pg_db, learner)
    stand.scenario.push_for(MATH, verdict("correct"))
    stand.writes([block("Pythagoras", formulas=[IDENTITY])])
    r = stand.resources
    notes, _cards = await refresh_topic(
        r.db, r.llm, TopicReader(topic_id, 1), _prefetch, stand.settings,
        learner_id=learner, topic_id=topic_id, key="job-1", compute=r.compute,
    )  # fmt: skip
    assert notes == 1 and len(stand.math_requests()) == 1


# --- the cheat sheet of a hub: the same code ------------------------------------------------


async def test_the_cheatsheet_checks_formulas_and_sources(stand: Stand, pg_db: Db) -> None:
    """WP23.5 uses the same checks: a wrong formula is regenerated, then marked; a definition
    without a chunk is regenerated, then dropped."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(MATH, verdict("incorrect"), verdict("incorrect"))
    stand.scenario.push_for(PDF, RenderPdfResponse(pdf_b64="", page_count=1))
    bare = block("Limit.", kind="definition", skills=["S1"])
    stand.writes([block("Pythagoras", formulas=[IDENTITY]), bare])
    r = stand.resources
    result = await generate_hub_cheatsheet(
        r.db, r.llm, r.compute, HubReader(hub), hub_prefetch, stand.settings,
        learner_id=learner, hub_id=hub, key="request-1",
    )  # fmt: skip
    assert result.version == 1 and stand.writer_calls == 2
    sheet = await get_artifact(pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub)
    (b,) = sheet.blocks
    assert (b.content, b.unverified, b.basis) == ("Pythagoras", True, "system")
    assert HUB_HIT.page == 3


async def test_the_review_keeps_the_marks_of_the_systems_blocks(stand: Stand, pg_db: Db) -> None:
    """The learner's own sheet is reviewed; the system's earlier blocks keep their marks."""
    learner = await create_learner(pg_db)
    hub, _ = await create_hub(pg_db, learner)
    stand.scenario.push_for(MATH, verdict("incorrect"), verdict("incorrect"))
    stand.scenario.push_for(PDF, RenderPdfResponse(pdf_b64="", page_count=1))
    stand.writes([block("Pythagoras", formulas=[IDENTITY])])
    r = stand.resources
    await generate_hub_cheatsheet(
        r.db, r.llm, r.compute, HubReader(hub), hub_prefetch, stand.settings,
        learner_id=learner, hub_id=hub, key="request-1",
    )  # fmt: skip
    sheet = await get_artifact(pg_db, learner_id=learner, kind="cheatsheet", owner_id=hub)
    await edit_block(
        pg_db, learner_id=learner, artifact_id=sheet.artifact_id, block_id=uuid4(),
        content="mine", idempotency_key="mine",
    )  # fmt: skip
    stand.writes([block("Remark", updates=2, kind="my_traps", sources=["P1"])])
    review = await review_hub_cheatsheet(
        r.db, r.llm, HubReader(hub), hub_prefetch, stand.settings,
        learner_id=learner, hub_id=hub, key="review-1", compute=r.compute,
    )  # fmt: skip
    kept = next(b for b in review.artifact.blocks if b.content == "Pythagoras")
    assert (kept.unverified, kept.basis) == (True, "system")
    (remark,) = review.remarks
    assert remark.basis == "materials"
