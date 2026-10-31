"""Tier and weight of an evidence item, assigned by code (law 4; ТЗ Фазы 3 WP12.2 п.3–4,
`Т-§7.2`, `Т-§7.4`, `Т-§7.8`). Pure functions: the model's answer never reaches them, and every
number comes from `Settings`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from quack.config import Settings

type Tier = Literal[1, 2, 3, 4]
# Where the evidence came from (`Т-§7.8`).
type EvidenceSource = Literal["code_verdict", "model_verdict", "extractor", "conversation"]
# Second opinion on a model verdict (`Т-§7.7`).
type SecondOpinion = Literal["not_requested", "agreed", "disagreed"]

MS_PER_MINUTE = 60_000


@dataclass(frozen=True)
class EvidenceWeightParams:
    """Numbers of `Т-§7.4` (starting values, `Т-§7.11`), read from `Settings`."""

    tier_weights: tuple[float, float, float, float]
    hint_step_penalty: float
    hot_window_ms: int
    hot_window_factor: float

    @classmethod
    def from_settings(cls, settings: Settings) -> EvidenceWeightParams:
        raise NotImplementedError


@dataclass(frozen=True)
class WeightedEvidence:
    """The categorical facts of one evidence item that its weight depends on."""

    tier: Tier
    at_ms: int
    options_count: int | None = None  # choice of k options; None for an open answer
    last_exposure_at_ms: int = 0  # last exposure of the skill before the answer; 0 = never
    hint_step: int | None = None  # 1..3
    shown_solution: bool = False


def assign_tier(source: EvidenceSource, second_opinion: SecondOpinion = "not_requested") -> Tier:
    """`Т-§7.8`: code verdict → T1; model verdict without or with an agreeing second opinion →
    T2, with a disagreeing one → T3; extractor element → T3; conversation → T4."""
    raise NotImplementedError


def evidence_weight(evidence: WeightedEvidence, params: EvidenceWeightParams) -> float:
    """`Т-§7.4`: tier base × guessing `1 − 1/k` × hot window × hint `1 − penalty·k`; a shown
    solution gives no weight of understanding."""
    raise NotImplementedError
