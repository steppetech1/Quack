"""`ComputeClient.math_check` and `math_transition` (ТЗ Фазы 4 WP24, WP11.5, seam S10, §8.1)."""

from __future__ import annotations

import json

import httpx
import pytest

from quack.compute_client.client import ComputeClient, ComputeJobTimeout, ComputeUnavailableError
from quack.compute_client.math_contracts import (
    MathCheckRequest,
    MathCheckResponse,
    MathInput,
    NumericParams,
    Tolerance,
    TransitionRequest,
    TransitionResponse,
)
from tests.support.compute import ComputeScenario, scripted_compute

NUMERIC = NumericParams(
    numeric_points=20,
    simplify_share=0.5,
    precision_digits=30,
    sample_low=-10.0,
    sample_high=10.0,
    positive_sample_min=0.1,
    max_attempts_per_point=10,
    rel_tol=1e-9,
    abs_tol=1e-12,
)
CHECK = MathCheckRequest(
    student=MathInput(sympy="2*x"),
    reference=MathInput(sympy="x + x"),
    tolerance=Tolerance(min_sig_digits=3),
    numeric=NUMERIC,
    timeout_ms=1500,
)
TRANSITION = TransitionRequest(
    previous=MathInput(sympy="2*x = 4"),
    current=MathInput(sympy="x = 2"),
    numeric=NUMERIC,
    timeout_ms=10_000,
)


async def test_math_methods_post_their_endpoint_and_parse_the_answer() -> None:
    scenario = ComputeScenario()
    verdict = MathCheckResponse(status="completed", outcome="correct", method="symbolic")
    follows = TransitionResponse(status="completed", follows=True)
    scenario.push_for("/v1/math/check", verdict)
    scenario.push_for("/v1/math/transition", follows)
    client = scripted_compute(scenario)
    try:
        assert await client.math_check(CHECK) == verdict
        assert await client.math_transition(TRANSITION) == follows
    finally:
        await client.aclose()
    sent = [(r.url.path, json.loads(r.content)) for r in scenario.requests]
    assert sent == [
        ("/v1/math/check", CHECK.model_dump(mode="json")),
        ("/v1/math/transition", TRANSITION.model_dump(mode="json")),
    ]


@pytest.mark.parametrize("method", ["math_check", "math_transition"])
async def test_math_methods_timeout_and_unavailable(method: str) -> None:
    path = "/v1/math/check" if method == "math_check" else "/v1/math/transition"
    request = CHECK if method == "math_check" else TRANSITION
    scenario = ComputeScenario()
    scenario.push_for(path, 504, 503)
    client = scripted_compute(scenario)
    try:
        with pytest.raises(ComputeJobTimeout):
            await getattr(client, method)(request)
        with pytest.raises(ComputeUnavailableError) as exc:
            await getattr(client, method)(request)
        assert not isinstance(exc.value, ComputeJobTimeout)
    finally:
        await client.aclose()


async def test_math_http_timeout_is_the_call_budget_plus_compute_timeout() -> None:
    """The HTTP wait is `timeout_ms / 1000 + timeout_sec`, not the 60 s job timeout."""
    seen: list[dict[str, float]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"status": "completed", "follows": True})

    client = ComputeClient(
        "http://compute.test",
        5.0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        job_timeout_sec=65.0,
    )
    try:
        await client.math_transition(TRANSITION)
    finally:
        await client.aclose()
    assert seen[0]["read"] == pytest.approx(10.0 + 5.0)
