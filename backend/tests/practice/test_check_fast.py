"""ТЗ Ф4 §8.7 T-CH2, T-CH3 (pure part): the fast check of `check()` through compute
(`/v1/math/check`, WP11.3, WP11.5; Т-§6.1, Т-§22.7).

The written halves — `verdict.recorded` before the turn, the insurance job `check.symbolic`
without duplicates, `check.failed` → `check.model` — need `Db` (S2) and the dispatcher (S6)
and land with them. Here: what `check()` sends to compute and what it returns.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from quack.compute_client.client import ComputeClient
from quack.compute_client.math_contracts import MathCheckResponse
from quack.config import Settings
from quack.practice.answer_spec import AnswerSpec, LearnerAnswer
from quack.practice.check import CheckFailed, NeedsModel, NotYet, Verdict, check
from tests.support.compute import ComputeScenario, scripted_compute

SETTINGS = Settings()
CHECK_PATH = "/v1/math/check"

EXPRESSION = AnswerSpec.model_validate(
    {
        "type": "expression",
        "value": {"latex": "2x", "sympy": "2*x"},
        "mode": "equal",
        "variables": [{"name": "x", "assumptions": ["real"]}],
    }
)
QUANTITY = AnswerSpec.model_validate(
    {"type": "quantity", "value": {"latex": "9.81", "sympy": "9.81"}, "unit": "m/s**2"}
)


async def _run(
    spec: AnswerSpec, answer: LearnerAnswer, *answers: MathCheckResponse | int
) -> tuple[Any, list[dict[str, Any]]]:
    scenario = ComputeScenario()
    scenario.push_for(CHECK_PATH, *answers)
    client = scripted_compute(scenario)
    try:
        result = await check(
            answer,
            spec,
            answer_seq=3,
            part="a",
            settings=SETTINGS,
            compute=client,
            timeout_ms=SETTINGS.symbolic_inline_timeout_ms,
        )
    finally:
        await client.aclose()
    sent = [json.loads(r.content) for r in scenario.requests if r.url.path == CHECK_PATH]
    return result, sent


async def test_expression_request_carries_spec_and_settings() -> None:
    answer = LearnerAnswer(kind="expression", value="x+x", latex="x+x", sympy="x + x")
    verdict = MathCheckResponse(status="completed", outcome="correct", method="symbolic")
    _, sent = await _run(EXPRESSION, answer, verdict)
    assert len(sent) == 1
    body = sent[0]
    assert body["kind"] == "expression"
    assert body["student"] == {"latex": "x+x", "sympy": "x + x"}
    assert body["reference"] == {"latex": "2x", "sympy": "2*x"}
    assert body["mode"] == "equal"
    assert body["variables"] == [{"name": "x", "assumptions": ["real"]}]
    assert body["timeout_ms"] == SETTINGS.symbolic_inline_timeout_ms
    assert body["tolerance"]["min_sig_digits"] == SETTINGS.min_sig_digits
    assert body["numeric"] == {
        "numeric_points": SETTINGS.numeric_points,
        "simplify_share": SETTINGS.math_simplify_share,
        "precision_digits": SETTINGS.math_precision_digits,
        "sample_low": SETTINGS.math_sample_low,
        "sample_high": SETTINGS.math_sample_high,
        "positive_sample_min": SETTINGS.math_positive_sample_min,
        "max_attempts_per_point": SETTINGS.math_max_attempts_per_point,
        "rel_tol": SETTINGS.math_rel_tol,
        "abs_tol": SETTINGS.math_abs_tol,
    }


async def test_quantity_request_sends_raw_answer_unit_and_spec_tolerance() -> None:
    spec = QUANTITY.model_copy(update={"sig_digits": 3})
    answer = LearnerAnswer(kind="quantity", value="9,8 m/s^2")
    verdict = MathCheckResponse(status="completed", outcome="correct", method="units")
    _, sent = await _run(spec, answer, verdict)
    body = sent[0]
    assert (body["kind"], body["student_text"], body["unit"]) == (
        "quantity",
        "9,8 m/s^2",
        "m/s**2",
    )
    assert body["reference"]["sympy"] == "9.81"
    # Т-§6.4: three significant digits of 9.81 — half a unit of the last one.
    assert body["tolerance"]["tol_abs"] == pytest.approx(0.005)


async def test_number_written_as_expression_goes_to_compute() -> None:
    """Т-§6.1: a number written as an expression is a fast check, mode `equal`."""
    spec = AnswerSpec.model_validate(
        {"type": "number", "value": {"latex": "\\frac{\\sqrt{2}}{2}", "sympy": "sqrt(2)/2"}}
    )
    answer = LearnerAnswer(kind="number", value="1/sqrt(2)", sympy="1/sqrt(2)")
    verdict = MathCheckResponse(status="completed", outcome="correct", method="symbolic")
    result, sent = await _run(spec, answer, verdict)
    assert sent[0]["kind"] == "expression" and sent[0]["mode"] == "equal"
    assert isinstance(result, Verdict) and result.outcome == "correct"


async def test_completed_answer_is_a_code_verdict() -> None:
    answer = LearnerAnswer(kind="expression", value="-2*x", sympy="-2*x")
    verdict = MathCheckResponse(
        status="completed",
        outcome="incorrect",
        method="symbolic",
        numeric_only=True,
        error_hint="sign",
    )
    result, _ = await _run(EXPRESSION, answer, verdict)
    assert result == Verdict(
        answer_seq=3,
        part="a",
        outcome="incorrect",
        method="symbolic",
        verification="code",
        numeric_only=True,
        error_hint="sign",
    )


@pytest.mark.parametrize("reason", ["unparsed", "too_few_points"])
async def test_not_parsed_or_too_few_points_is_check_failed(reason: str) -> None:
    """T-CH3, Т-§22.7: `check.failed`, not `unclear`."""
    answer = LearnerAnswer(kind="expression", value="2x(", sympy="2*x(")
    failed = MathCheckResponse(status="failed", reason=reason)  # type: ignore[arg-type]
    result, _ = await _run(EXPRESSION, answer, failed)
    assert result == CheckFailed(answer_seq=3, part="a", reason=reason)  # type: ignore[arg-type]


async def test_unsupported_form_goes_to_the_model() -> None:
    """Т-§6.6: no structural check for the form — the model decides (class model)."""
    answer = LearnerAnswer(kind="expression", value="x+x", sympy="x + x")
    failed = MathCheckResponse(status="failed", reason="unsupported")
    result, _ = await _run(EXPRESSION, answer, failed)
    assert result == NeedsModel(answer_seq=3, part="a")


@pytest.mark.parametrize(("status", "reason"), [(504, "timeout"), (503, "compute_unavailable")])
async def test_compute_timeout_or_down_writes_nothing_now(status: int, reason: str) -> None:
    """Т-§6.1: no verdict in time — nothing is written; the insurance job checks later."""
    answer = LearnerAnswer(kind="expression", value="x+x", sympy="x + x")
    result, _ = await _run(EXPRESSION, answer, status)
    assert result == NotYet(answer_seq=3, part="a", reason=reason)  # type: ignore[arg-type]


async def test_inline_wait_is_cut_at_timeout_ms() -> None:
    """T-CH2: the API waits not longer than `timeout_ms`, then cancels the request."""
    cancelled = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return httpx.Response(200)

    client = ComputeClient(
        "http://compute.test", client=httpx.AsyncClient(transport=httpx.MockTransport(slow))
    )
    answer = LearnerAnswer(kind="expression", value="x+x", sympy="x + x")
    try:
        result = await asyncio.wait_for(
            check(
                answer,
                EXPRESSION,
                answer_seq=3,
                part="a",
                settings=SETTINGS,
                compute=client,
                timeout_ms=50,
            ),
            timeout=1,
        )
    finally:
        await client.aclose()
    assert result == NotYet(answer_seq=3, part="a", reason="timeout")
    assert cancelled.is_set()


async def test_set_of_expressions_is_check_failed_without_compute() -> None:
    """A `set` / `vector` of non-numbers is not an instant type and compute has no mode for
    it (D-B-126): `check.failed` → the model."""
    spec = AnswerSpec.model_validate(
        {"type": "set", "items": [{"latex": "\\sqrt{2}", "sympy": "sqrt(2)"}]}
    )
    answer = LearnerAnswer(kind="set", value="{sqrt(2)}")
    result, sent = await _run(spec, answer)
    assert result == CheckFailed(answer_seq=3, part="a", reason="unparsed")
    assert sent == []


async def test_decimal_against_an_expression_reference_gets_the_tolerance_rules() -> None:
    """Т-§6.4: 0.7071 for `sqrt(2)/2` is a number with its own precision, not an expression to
    compare at `math_rel_tol`: it goes to compute as a dimensionless value with the tolerance
    of the spec, and the verdict is a `numeric` one."""
    spec = AnswerSpec.model_validate(
        {
            "type": "number",
            "value": {"latex": "\\frac{\\sqrt{2}}{2}", "sympy": "sqrt(2)/2"},
            "tol_rel": 0.001,
        }
    )
    answer = LearnerAnswer(kind="number", value="0,7071")
    done = MathCheckResponse(status="completed", outcome="correct", method="units")
    verdict, sent = await _run(spec, answer, done)
    [body] = sent
    assert (body["kind"], body["unit"], body["student_text"]) == (
        "quantity",
        "dimensionless",
        "0,7071",
    )
    assert body["reference"]["sympy"] == "sqrt(2)/2"
    assert (body["tolerance"]["tol_abs"], body["tolerance"]["tol_rel"]) == (None, 0.001)
    assert isinstance(verdict, Verdict)
    assert (verdict.outcome, verdict.method) == ("correct", "numeric")


async def test_expression_answer_for_a_number_stays_in_the_expression_mode() -> None:
    spec = AnswerSpec.model_validate(
        {"type": "number", "value": {"latex": "\\frac{\\sqrt{2}}{2}", "sympy": "sqrt(2)/2"}}
    )
    answer = LearnerAnswer(kind="number", value="2/sqrt(8)", sympy="2/sqrt(8)")
    done = MathCheckResponse(status="completed", outcome="correct", method="symbolic")
    verdict, sent = await _run(spec, answer, done)
    assert sent[0]["kind"] == "expression"
    assert verdict.method == "symbolic"
