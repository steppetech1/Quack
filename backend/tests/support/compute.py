"""compute stub on `httpx.MockTransport` with a scenario of verdicts (ТЗ Фазы 3 §8.1)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

import httpx

from quack.compute_client.client import ComputeClient, MathCheckResponse

# A response body, or an HTTP status code to answer with (e.g. 503 = compute is down).
type ComputeVerdict = MathCheckResponse | int


def math_verdict(is_equivalent: bool | None, status: str = "completed") -> MathCheckResponse:
    return MathCheckResponse.model_validate(
        {
            "status": status,
            "is_equivalent": is_equivalent,
            "normalized_student": None,
            "diff_hint": None,
            "error_message": None,
        }
    )


class ComputeScenario:
    """Verdicts returned in order by `POST /v1/check/math`; an empty scenario answers 503."""

    def __init__(self, verdicts: Iterable[ComputeVerdict] = ()) -> None:
        self.verdicts: deque[ComputeVerdict] = deque(verdicts)
        self.requests: list[httpx.Request] = []

    def push(self, *verdicts: ComputeVerdict) -> None:
        self.verdicts.extend(verdicts)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method != "POST" or request.url.path != "/v1/check/math":
            return httpx.Response(404)
        if not self.verdicts:
            return httpx.Response(503)
        verdict = self.verdicts.popleft()
        if isinstance(verdict, int):
            return httpx.Response(verdict)
        return httpx.Response(200, json=verdict.model_dump(mode="json"))


def scripted_compute(
    scenario: ComputeScenario, base_url: str = "http://compute.test"
) -> ComputeClient:
    """`ComputeClient` whose only `httpx.AsyncClient` goes to `scenario` (§4.2)."""
    return ComputeClient(
        base_url, client=httpx.AsyncClient(transport=httpx.MockTransport(scenario.handler))
    )
