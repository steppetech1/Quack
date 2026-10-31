"""Stand-in Meta API server `meta-mock` (ТЗ Фазы 3 WP10.3, Д-§11, Т-§11.8): stage I."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from starlette.applications import Starlette

type Latency = Literal["measured", "zero"]
type RetryAfterFormat = Literal["seconds", "http-date", "none", "mixed"]


@dataclass(frozen=True)
class MetaMockConfig:
    latency: Latency = "measured"
    rpm: int = 3000
    tpm: int = 4_000_000
    shared_limit: bool = True
    share_429: float = 0.0
    retry_after: RetryAfterFormat = "mixed"
    incomplete_share: float = 0.0
    teacher_chars: int = 600

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> MetaMockConfig:
        raise NotImplementedError


def minimal_instance(schema: dict[str, Any], *, text_chars: int | None = None) -> Any:
    raise NotImplementedError


def latency_s(model: str, effort: str, latency: Latency) -> float:
    raise NotImplementedError


def create_app(config: MetaMockConfig | None = None) -> Starlette:
    raise NotImplementedError
