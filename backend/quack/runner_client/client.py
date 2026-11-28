"""HTTP client of the runner (Т-§6.10). The runner has no network: in compose it listens on a
Unix socket of a shared volume (`RUNNER_URL=unix:/run/quack-runner/runner.sock`)."""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ValidationError

from quack.config import Settings
from quack.runner_client.contracts import (
    PythonRunRequest,
    PythonRunResponse,
    SqlRunRequest,
    SqlRunResponse,
)

UNIX_PREFIX = "unix:"


class RunnerUnavailableError(Exception):
    """The runner did not answer in time, refused, or answered garbage: nothing is decided."""


class RunnerBusyError(RunnerUnavailableError):
    """All slots of the runner are busy (HTTP 429)."""


class RunnerClient:
    def __init__(
        self, base_url: str, timeout_sec: float, client: httpx.AsyncClient | None = None
    ) -> None:
        if client is not None:
            self.base_url = base_url.rstrip("/")
            self.client = client
        elif base_url.startswith(UNIX_PREFIX):
            transport = httpx.AsyncHTTPTransport(uds=base_url.removeprefix(UNIX_PREFIX))
            self.base_url = "http://runner"
            self.client = httpx.AsyncClient(transport=transport)
        else:
            self.base_url = base_url.rstrip("/")
            self.client = httpx.AsyncClient()
        self.timeout_sec = timeout_sec

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _post[R: BaseModel](self, path: str, body: BaseModel, response: type[R]) -> R:
        try:
            reply = await self.client.post(
                f"{self.base_url}{path}",
                json=body.model_dump(mode="json"),
                timeout=self.timeout_sec,
            )
        except httpx.HTTPError as exc:
            raise RunnerUnavailableError(repr(exc)) from exc
        if reply.status_code == 429:
            raise RunnerBusyError("all runner slots are busy")
        if reply.status_code != 200:
            raise RunnerUnavailableError(f"runner answered {reply.status_code}")
        try:
            return response.model_validate(reply.json())
        except (ValueError, ValidationError) as exc:
            raise RunnerUnavailableError(f"runner answer is not {response.__name__}") from exc

    async def run_python(self, request: PythonRunRequest) -> PythonRunResponse:
        return await self._post("/v1/run/python", request, PythonRunResponse)

    async def run_sql(self, request: SqlRunRequest) -> SqlRunResponse:
        return await self._post("/v1/run/sql", request, SqlRunResponse)


def create_runner_client(settings: Settings) -> RunnerClient:
    """The process-wide `RunnerClient` (§4.2)."""
    return RunnerClient(settings.runner_url, settings.runner_timeout_sec)
