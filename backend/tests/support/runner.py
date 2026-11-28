"""A fake runner for the contract walks (Т-§6.10): the real `RunnerClient` over a mock HTTP
transport. Every test of a request comes back `ok` with the same value; no code is run."""

from __future__ import annotations

import json
from typing import Any

import httpx

from quack.runner_client.client import RunnerClient


def fake_runner(value: Any = 4) -> RunnerClient:
    def respond(request: httpx.Request) -> httpx.Response:
        tests = json.loads(request.content)["tests"]
        result = {"status": "ok", "value": value, "stdout": "", "stderr": "", "time_ms": 1}
        return httpx.Response(200, json={"tests": [result for _ in tests]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    return RunnerClient("http://runner", 5.0, client=client)
