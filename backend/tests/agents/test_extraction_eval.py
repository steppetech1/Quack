"""Quality of the extraction on the reference set (ТЗ Ф4 WP16.7; Т-§7.11; D-B-209): precision of
negative elements and hypotheses, Cohen's kappa between runs, the share of code rejections — by
hand on a scripted extractor; the comparison of two versions; no data is «not measured»."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import orjson
import pytest
from pydantic import BaseModel

from quack.agents.extraction_eval import (
    EvalSet,
    cohen_kappa,
    compare_reports,
    evaluate_extraction,
    load_eval_set,
)
from quack.agents.schemas import ExtractedElement, ExtractorOutputV3
from quack.config import Settings
from quack.llm.gateway import LLMGateway, LLMResult

SETTINGS = Settings()


def _el(**kw: Any) -> ExtractedElement:
    base: dict[str, Any] = {
        "kind": "evidence",
        "skill": "S1",
        "new_skill": None,
        "skill_unclear": False,
        "level": 2,
        "outcome": "incorrect",
        "help": "none",
        "hint_step": None,
        "gap_type": "confusion",
        "misconception": None,
        "new_misconception": None,
        "root_cause": None,
        "quote": "root test",
        "events": [1],
    }
    base.update(kw)
    return ExtractedElement.model_validate(base)


E1 = _el()
POSITIVE = _el(outcome="correct", gap_type="none")
HYPOTHESIS = _el(
    kind="misconception_hypothesis",
    level=None,
    outcome=None,
    gap_type="misconception",
    misconception="M1",
)
WRONG = _el(skill="S2", level=1)  # a negative element nobody expected
UNKNOWN = _el(skill="S9")  # rejected by the check: no such label


@dataclass
class _Gateway:
    """The extractor scripted per call; it records the calls and has no database at all."""

    outputs: list[list[ExtractedElement]]
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def generate_structured(
        self,
        role: str,
        prompt_version: int,
        response_model: type[BaseModel],
        variables: dict[str, Any],
        learner_id: UUID,
        session_id: UUID | None,
        turn_id: UUID | None,
        idempotency_key: str,
        temperature: float = 0.0,
        **kwargs: Any,
    ) -> LLMResult[Any]:
        self.calls.append(
            {"role": role, "version": prompt_version, "key": idempotency_key, **kwargs}
        )
        value = ExtractorOutputV3(elements=self.outputs[len(self.calls) - 1])
        return LLMResult(value=value, from_cache=False, prompt_hash="h", model="m", usage=None)


def _set() -> EvalSet:
    return EvalSet.model_validate(
        {
            "name": "episodes-test",
            "episodes": [
                {
                    "episode_id": "ep1",
                    "variables": {"episode": "[1] learner: root test"},
                    "expected": [
                        {"kind": "evidence", "skill": "S1", "level": 2, "outcome": "incorrect"},
                        {"kind": "misconception_hypothesis", "skill": "S1", "misconception": "M1"},
                    ],
                    "check": {
                        "skill_labels": ["S1", "S2"],
                        "misconception_labels": ["M1"],
                        "episode": [{"seq": 1, "text": "root test"}],
                    },
                }
            ],
        }
    )


async def test_extraction_eval_by_hand(tmp_path: Path) -> None:
    """Two runs of one episode, counted by hand: of 5 accepted negative elements 4 match
    (evidence 2 of 3, hypotheses 2 of 2); the positive element and the rejected one do not
    count; kappa of the gap type 0.2, of presence -3/7; 1 of 7 elements rejected."""
    gateway = _Gateway(
        [
            [E1, POSITIVE, HYPOTHESIS, WRONG, UNKNOWN],
            [E1.model_copy(update={"gap_type": "slip"}), HYPOTHESIS],
        ]
    )
    out = tmp_path / "report.json"
    report = await evaluate_extraction(
        cast(LLMGateway, gateway), SETTINGS, _set(), learner_id=uuid4(), runs=2, out=out
    )
    assert report["precision"] == {"status": "measured", "value": 0.8, "count": 4, "of": 5}
    assert report["precision_evidence"]["value"] == pytest.approx(2 / 3)
    assert report["precision_hypothesis"]["value"] == 1.0
    assert report["recall"]["value"] == 1.0
    fields = report["kappa"]["fields"]
    assert fields["gap_type"]["value"] == pytest.approx(0.2)
    assert fields["presence"]["value"] == pytest.approx(-3 / 7)
    assert fields["help"]["value"] == 1.0
    assert report["kappa"]["value"] == pytest.approx((-3 / 7 + 4.2) / 6)
    assert report["code_rejections"] == {"status": "measured", "value": 1 / 7, "count": 1, "of": 7}
    assert orjson.loads(out.read_bytes())["precision"]["value"] == 0.8
    # the role and the version of the prompt; a key per run; the extractor is checked for labels
    assert {c["role"] for c in gateway.calls} == {"extractor"}
    assert gateway.calls[0]["version"] == 3
    assert len({c["key"] for c in gateway.calls}) == 2 and "extractor@3" in gateway.calls[0]["key"]
    assert gateway.calls[0]["check"](ExtractorOutputV3(elements=[UNKNOWN])) is not None


def test_cohen_kappa() -> None:
    assert cohen_kappa([("a", "a"), ("b", "b")]) == 1.0
    assert cohen_kappa([("a", "b"), ("b", "a")]) == -1.0
    assert cohen_kappa([]) is None
    assert SETTINGS.extraction_eval_runs == 5


async def test_not_measured_and_comparison(tmp_path: Path) -> None:
    """No episodes or one run — «not measured», not a zero; the comparison flags a drop of
    precision or kappa and says nothing when there is nothing to compare."""
    empty = EvalSet(name="empty", episodes=[])
    gateway = cast(LLMGateway, _Gateway([]))
    report = await evaluate_extraction(gateway, SETTINGS, empty, learner_id=uuid4())
    assert report["precision"]["status"] == "not_measured"
    assert report["kappa"]["status"] == "not_measured"
    one = await evaluate_extraction(
        cast(LLMGateway, _Gateway([[E1]])), SETTINGS, _set(), learner_id=uuid4(), runs=1
    )
    assert one["kappa"]["status"] == "not_measured" and one["precision"]["status"] == "measured"
    assert compare_reports(report, report)["regression"] is None

    def rep(precision: float, kappa: float) -> dict[str, Any]:
        measured = lambda v: {"status": "measured", "value": v}  # noqa: E731
        return {
            "fingerprint": "f",
            "prompt": "extractor@3",
            "precision": measured(precision),
            "kappa": measured(kappa),
        }

    assert compare_reports(rep(0.8, 0.6), rep(0.8, 0.6))["regression"] is False
    assert compare_reports(rep(0.8, 0.6), rep(0.9, 0.7))["regression"] is False
    worse = compare_reports(rep(0.8, 0.6), rep(0.8, 0.5))
    assert worse["regression"] is True and worse["metrics"]["kappa"]["dropped"] is True
    assert compare_reports(rep(0.8, 0.6), rep(0.7, 0.9))["regression"] is True
    other = {**rep(0.9, 0.9), "fingerprint": "g"}
    assert compare_reports(rep(0.8, 0.6), other)["regression"] is None

    path = tmp_path / "set.json"
    path.write_bytes(orjson.dumps(_set().model_dump(mode="json")))
    assert load_eval_set(path) == _set()
