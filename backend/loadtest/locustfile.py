"""Locust scenario of the load stand (ТЗ Фазы 3 WP10.3, `Д-§11`).

    LOADTEST_LEARNERS=learners.json uv run locust -f loadtest/locustfile.py --host http://api:8000

`learners.json` is written by `ops seed-learners --count N --out learners.json` (WP8.6). The
logic lives in `loadtest/scenario.py`; this file only binds it to Locust. Besides the HTTP
requests the report has three SSE rows: `turn: status` (`teacher_status_p95_ms`, WP6.3),
`turn: first token` and `turn: complete` (a failure there is a cut stream).
"""

from __future__ import annotations

import itertools
import os
import random
import time
from pathlib import Path
from uuid import uuid4

from locust import HttpUser, constant_pacing, task

from loadtest.scenario import (
    TASK_SHARE,
    TURN_INTERVAL_S,
    TURN_TEXT,
    TURNS_PER_SESSION,
    Learner,
    key_body,
    load_learners,
    next_task_body,
    read_turn_stream,
    session_start_body,
    submit_body,
    turn_body,
)

_LEARNERS: list[Learner] = []
_NEXT = itertools.count()


def _take_learner() -> Learner:
    if not _LEARNERS:
        _LEARNERS.extend(load_learners(Path(os.environ["LOADTEST_LEARNERS"])))
    return _LEARNERS[next(_NEXT) % len(_LEARNERS)]


class LearnerUser(HttpUser):
    wait_time = constant_pacing(TURN_INTERVAL_S)

    def on_start(self) -> None:
        self.learner = _take_learner()
        self.session_id: str | None = None
        self.turns = 0
        self._start_session()

    def _key(self, what: str) -> str:
        return f"lt:{self.learner.learner_id}:{what}:{uuid4().hex}"

    def _start_session(self) -> None:
        topic_id = random.choice(self.learner.topic_ids)
        body = session_start_body(self.learner.domain_id, topic_id, self._key("session"))
        with self.client.post(
            "/v1/sessions",
            json=body,
            headers=self.learner.headers,
            name="/v1/sessions",
            catch_response=True,
        ) as response:
            if response.status_code != 201:
                response.failure(f"start session: {response.status_code}")
                return
            self.session_id = response.json()["session_id"]
            self.topic_id = topic_id
            self.turns = 0

    def _sse(self, name: str, elapsed_ms: float | None, error: str | None = None) -> None:
        self.environment.events.request.fire(
            request_type="SSE",
            name=name,
            response_time=elapsed_ms or 0.0,
            response_length=0,
            exception=None if error is None else RuntimeError(error),
            context={},
        )

    def _turn(self) -> str | None:
        """One turn over SSE; the `turn_id` for a task, `None` if the stream was cut."""
        path = f"/v1/sessions/{self.session_id}/turns"
        started_at = time.monotonic()
        with self.client.post(
            path,
            json=turn_body(TURN_TEXT, self._key("turn")),
            headers=self.learner.headers,
            name="/v1/sessions/{id}/turns",
            stream=True,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"turn: {response.status_code}")
                return None
            stream = read_turn_stream(response.iter_lines(), started_at=started_at)
            response.success()
        self._sse("turn: status", stream.status_ms)
        self._sse("turn: first token", stream.token_ms)
        if not stream.complete:
            self._sse("turn: complete", None, f"cut after {stream.events}")
            return None
        self._sse("turn: complete", (time.monotonic() - started_at) * 1000.0)
        return stream.turn_id

    def _task(self, turn_id: str) -> None:
        response = self.client.post(
            f"/v1/sessions/{self.session_id}/tasks/next",
            json=next_task_body(self.topic_id, turn_id, self._key("task")),
            headers=self.learner.headers,
            name="/v1/sessions/{id}/tasks/next",
        )
        if response.status_code != 200:
            return
        self.client.post(
            f"/v1/sessions/{self.session_id}/tasks/{response.json()['task_id']}/submit",
            json=submit_body(turn_id, self._key("submit")),
            headers=self.learner.headers,
            name="/v1/sessions/{id}/tasks/{task_id}/submit",
        )

    @task
    def learn(self) -> None:
        if self.session_id is None:
            self._start_session()
            return
        self.client.post(
            f"/v1/sessions/{self.session_id}/heartbeat",
            headers=self.learner.headers,
            name="/v1/sessions/{id}/heartbeat",
        )
        turn_id = self._turn()
        self.turns += 1
        if turn_id is not None and random.random() < TASK_SHARE:
            self._task(turn_id)
        if self.turns >= TURNS_PER_SESSION:
            self.client.post(
                f"/v1/sessions/{self.session_id}/finish",
                json=key_body(self._key("finish")),
                headers=self.learner.headers,
                name="/v1/sessions/{id}/finish",
            )
            self.session_id = None
