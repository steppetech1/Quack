"""compute stub on `httpx.MockTransport` with a scenario of verdicts (ТЗ Фазы 3 §8.1) and of
answers for every WP24 endpoint (ТЗ Фазы 4 §8.1, seam S10)."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable

import httpx
from pydantic import BaseModel

from quack.compute_client.client import ComputeClient, MathCheckResponse

# A response body, or an HTTP status code to answer with (e.g. 503 = compute is down,
# 504 = the job was killed on its timeout).
type ComputeVerdict = MathCheckResponse | int
type ComputeAnswer = BaseModel | int

# WP24 endpoints of the stub; `/v1/check/math` keeps its own queue (`verdicts`).
WP24_PATHS = (
    "/v1/pdf/pages",
    "/v1/image/align",
    "/v1/image/clean-drawing",
    "/v1/image/render-strokes",
    "/v1/render/snapshots",
    "/v1/render/pdf",
)


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
    """Verdicts returned in order by `POST /v1/check/math`, answers in order per WP24 endpoint
    (`push_for`); an empty queue answers 503."""

    def __init__(self, verdicts: Iterable[ComputeVerdict] = ()) -> None:
        self.verdicts: deque[ComputeVerdict] = deque(verdicts)
        self.answers: dict[str, deque[ComputeAnswer]] = {path: deque() for path in WP24_PATHS}
        self.requests: list[httpx.Request] = []

    def push(self, *verdicts: ComputeVerdict) -> None:
        self.verdicts.extend(verdicts)

    def push_for(self, path: str, *answers: ComputeAnswer) -> None:
        self.answers[path].extend(answers)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method != "POST":
            return httpx.Response(404)
        if path == "/v1/check/math":
            queue: deque[ComputeVerdict] | deque[ComputeAnswer] = self.verdicts
        elif path in self.answers:
            queue = self.answers[path]
        else:
            return httpx.Response(404)
        if not queue:
            return httpx.Response(503)
        answer = queue.popleft()
        if isinstance(answer, int):
            return httpx.Response(answer)
        return httpx.Response(200, json=answer.model_dump(mode="json"))


def scripted_compute(
    scenario: ComputeScenario, base_url: str = "http://compute.test"
) -> ComputeClient:
    """`ComputeClient` whose only `httpx.AsyncClient` goes to `scenario` (§4.2)."""
    return ComputeClient(
        base_url, client=httpx.AsyncClient(transport=httpx.MockTransport(scenario.handler))
    )
