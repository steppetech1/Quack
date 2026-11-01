"""Tier and weight of an evidence item, assigned by code (law 4; ТЗ Фазы 3 WP12.2 п.3–4,
`Т-§7.2`, `Т-§7.4`, `Т-§7.8`). Pure functions: the model's answer never reaches them, and every
number comes from `Settings`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from quack.config import FoldParams, Settings

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
        return cls.from_fold(settings.fold)

    @classmethod
    def from_fold(cls, fold: FoldParams) -> EvidenceWeightParams:
        """`Settings` copies the §6.2 parameters into `FoldParams` (WP12)."""
        return cls(
            tier_weights=fold.tier_base_weights,
            hint_step_penalty=fold.hint_step_penalty,
            hot_window_ms=fold.hot_window_min * MS_PER_MINUTE,
            hot_window_factor=fold.hot_window_factor,
        )


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
    match source:
        case "code_verdict":
            return 1
        case "model_verdict":
            return 3 if second_opinion == "disagreed" else 2
        case "extractor":
            return 3
        case "conversation":
            return 4


def evidence_weight(evidence: WeightedEvidence, params: EvidenceWeightParams) -> float:
    """`Т-§7.4`: tier base × guessing `1 − 1/k` × hot window × hint `1 − penalty·k`; a shown
    solution gives no weight of understanding."""
    if evidence.shown_solution:
        return 0.0
    weight = params.tier_weights[evidence.tier - 1]
    k = evidence.options_count
    if k is not None and k >= 2:
        weight *= 1.0 - 1.0 / k
    exposure = evidence.last_exposure_at_ms
    if exposure > 0 and 0 <= evidence.at_ms - exposure <= params.hot_window_ms:
        weight *= params.hot_window_factor
    if evidence.hint_step is not None:
        weight *= max(0.0, 1.0 - params.hint_step_penalty * evidence.hint_step)
    return weight
