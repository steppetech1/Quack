"""Self-checks of the shared session fixtures that need no infrastructure."""

from __future__ import annotations

from uuid import uuid4

import jwt

from quack.api.sse import format_sse
from quack.config import Settings
from tests.session.helpers import DEV_JWT_SECRET, make_token, parse_sse

pytest_plugins = ["tests.session.fixtures"]


def test_parse_sse_round_trips_format_sse() -> None:
    body = format_sse("turn_started", {"turn_id": "t"}) + format_sse("done", {})
    assert parse_sse(body) == [("turn_started", {"turn_id": "t"}), ("done", {})]


def test_token_uses_dev_secret_when_jwt_secret_is_empty() -> None:
    learner = uuid4()
    token = make_token(Settings(env="test"), learner)
    assert jwt.decode(token, DEV_JWT_SECRET, algorithms=["HS256"])["sub"] == str(learner)
