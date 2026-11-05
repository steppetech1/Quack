"""Phase B: the fold of the `Profile` node (Т-§9.5, ТЗ Ф4 WP5.3). Pure: no I/O, no current time.

The learned part (`learned*`) is code over the journal and the evidence, identical on rebuild
(Т-§2.5, "why the learned part is code, not a model"). Inputs are plain samples; the same events
and evidence in any arrival order give the same result, because every sample is ordered by its
`seq`. `None` is "no data" (law 8) and is **not written**: an absent property reads as `None`.

Where each input comes from:
- turns (`approach`, `branchType`, target skills): `turn.committed.head`;
- answers with reasoning: `answer.submitted.text`, `element.responded.text`;
- self-assessments 1–4: `answer.submitted.confidence`, `element.responded.confidence`,
  `confidence.reported {target_seq, value}` (the latest report of a source wins);
- evidence: the direct, active `Evidence` nodes with the gate flag computed by the skill fold
  (Т-§7.3, WP4); this module does not recompute the gate.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from quack.config import Settings
from quack.events.event_types import (
    AnswerSubmitted,
    ConfidenceReported,
    DigestWritten,
    ElementResponded,
    PreferenceSet,
    TurnCommitted,
)
from quack.events.registry import validate_payload
from quack.graph.ids import deterministic_id
from quack.graph.types import Props, PropValue


class JournalEvent(Protocol):
    """What the fold reads of an event: the stored event of Phase 3 or of `rules@3`."""

    @property
    def seq(self) -> int: ...
    @property
    def type(self) -> str: ...
    @property
    def schema_version(self) -> int: ...
    @property
    def payload(self) -> dict[str, Any]: ...


# Т-§9.5: self-assessments 1–4 as probabilities.
CONFIDENCE_PROBABILITY: dict[int, float] = {1: 0.25, 2: 0.5, 3: 0.75, 4: 0.95}

# Event types the profile fold reads (the projector marks the profile dirty on them).
PROFILE_EVENT_TYPES = frozenset(
    {
        "preference.set",
        "digest.written",
        "turn.committed",
        "answer.submitted",
        "element.responded",
        "confidence.reported",
    }
)
# Evidence changes (a new gate-passing outcome, a superseded one) also change the learned part.

LEARNED_KEYS = (
    "learnedHelpfulApproach",
    "learnedConfidenceBrier",
    "learnedConfidenceBias",
    "learnedBranchRate",
    "learnedReasoningShare",
)

_BRIER_DIGITS = 6
_BIAS_EPS = 1e-9


@dataclass(frozen=True)
class ProfileParams:
    """`profile_min_samples`, `calibration_window`, `calibration_min_reports` (Т-§9.5)."""

    profile_min_samples: int = 10
    calibration_window: int = 200
    calibration_min_reports: int = 10

    @classmethod
    def from_settings(cls, settings: Settings) -> ProfileParams:
        return cls(
            settings.profile_min_samples,
            settings.calibration_window,
            settings.calibration_min_reports,
        )


@dataclass(frozen=True)
class TurnSample:
    """One teacher turn: `head.approach`, `head.branch_type`, `head.target_skill_ids`."""

    seq: int
    approach: str
    branch_type: str | None
    target_skill_ids: tuple[str, ...]


@dataclass(frozen=True)
class AnswerSample:
    """A learner answer (`answer.submitted`, `element.responded`): was reasoning given."""

    seq: int
    reasoned: bool


@dataclass(frozen=True)
class ConfidenceSample:
    """A self-assessment 1–4 of the action `target_seq` (the source of evidence, `sourceSeq`);
    `seq` — the event that carries it (the latest one for a target wins)."""

    seq: int
    target_seq: int
    value: int


@dataclass(frozen=True)
class EvidenceSample:
    """A direct, active evidence. `source_seq` — the action it was made from (`sourceSeq`);
    `passes_gate` — the gate flag of the skill fold (Т-§7.3); `assistance` is `none`, `hint` or
    `shown_solution`."""

    source_seq: int
    skill_id: str
    tier: int
    outcome: Literal["correct", "incorrect", "partial", "unclear"]
    assistance: str = "none"
    passes_gate: bool = False


@dataclass(frozen=True)
class ProfileInputs:
    turns: tuple[TurnSample, ...] = ()
    answers: tuple[AnswerSample, ...] = ()
    confidences: tuple[ConfidenceSample, ...] = ()
    evidence: tuple[EvidenceSample, ...] = ()
    prefs: dict[str, PropValue] = field(default_factory=dict)
    manner: str | None = None


@dataclass(frozen=True)
class ProfileFold:
    """The learned part; `None` — no data."""

    helpful_approach: str | None = None
    confidence_brier: float | None = None
    confidence_bias: Literal["over", "under", "calibrated"] | None = None
    branch_rate: float | None = None
    reasoning_share: float | None = None

    def to_props(self) -> Props:
        """Properties of the `Profile` node; "no data" fields are left out."""
        values: dict[str, PropValue | None] = {
            "learnedHelpfulApproach": self.helpful_approach,
            "learnedConfidenceBrier": self.confidence_brier,
            "learnedConfidenceBias": self.confidence_bias,
            "learnedBranchRate": self.branch_rate,
            "learnedReasoningShare": self.reasoning_share,
        }
        return {k: v for k, v in values.items() if v is not None}

    def unset_keys(self) -> tuple[str, ...]:
        """`learned*` properties that must be removed from the node (no data now)."""
        present = self.to_props()
        return tuple(k for k in LEARNED_KEYS if k not in present)


# --- collecting inputs from the journal ----------------------------------------------------


def profile_id(learner_id: object) -> str:
    """The only `Profile` of a learner (1:1): UUIDv5 of the learner (D-A-130)."""
    return deterministic_id(str(learner_id), 0, "profile")


def pref_key(key: str) -> str:
    """`pref` + the key in PascalCase: `language` -> `prefLanguage` (WP5.3)."""
    return "pref" + "".join(part.capitalize() for part in key.split("_"))


def collect_profile_inputs(
    events: Iterable[JournalEvent], evidence: Sequence[EvidenceSample] = ()
) -> ProfileInputs:
    """Journal events (any order, any superset: unrelated types are skipped) plus the evidence
    of the learner -> fold inputs. The latest `preference.set` per key and the latest
    `digest.written` win; the latest self-assessment per target wins."""
    turns: list[TurnSample] = []
    answers: list[AnswerSample] = []
    reports: dict[int, ConfidenceSample] = {}
    prefs: dict[str, PropValue] = {}
    manner: str | None = None
    manner_seq = 0
    pref_seqs: dict[str, int] = {}

    def report(sample: ConfidenceSample) -> None:
        known = reports.get(sample.target_seq)
        if known is None or sample.seq > known.seq:
            reports[sample.target_seq] = sample

    for event in sorted(events, key=lambda e: e.seq):
        if event.type not in PROFILE_EVENT_TYPES:
            continue
        payload = validate_payload(event.type, event.payload, event.schema_version)
        if isinstance(payload, TurnCommitted):
            head = payload.head
            turns.append(
                TurnSample(
                    event.seq,
                    head.approach,
                    head.branch_type,
                    tuple(sorted(str(s) for s in head.target_skill_ids)),
                )
            )
        elif isinstance(payload, AnswerSubmitted | ElementResponded):
            answers.append(AnswerSample(event.seq, bool((payload.text or "").strip())))
            if payload.confidence is not None:
                report(ConfidenceSample(event.seq, event.seq, payload.confidence))
        elif isinstance(payload, ConfidenceReported):
            report(ConfidenceSample(event.seq, payload.target_seq, payload.value))
        elif isinstance(payload, PreferenceSet):
            if event.seq > pref_seqs.get(payload.key, 0):
                pref_seqs[payload.key] = event.seq
                prefs[pref_key(payload.key)] = payload.value
        elif isinstance(payload, DigestWritten) and event.seq > manner_seq:
            manner_seq = event.seq
            manner = payload.manner
    return ProfileInputs(
        turns=tuple(turns),
        answers=tuple(answers),
        confidences=tuple(sorted(reports.values(), key=lambda c: (c.target_seq, c.seq))),
        evidence=tuple(sorted(evidence, key=_evidence_order)),
        prefs=dict(sorted(prefs.items())),
        manner=manner,
    )


def _evidence_order(e: EvidenceSample) -> tuple[int, str, int, str, str]:
    return (e.source_seq, e.skill_id, e.tier, e.outcome, e.assistance)


# --- the fold ------------------------------------------------------------------------------


def _helpful_approach(inputs: ProfileInputs, params: ProfileParams) -> str | None:
    """After which `approach` an independent T1 success on the same skill follows most often
    (Т-§9.5). A sample is a (turn, target skill) pair with a T1 attempt of that skill after the
    turn and before the next turn aimed at the same skill: its first attempt is a success if it
    is correct without help. An approach is judged from `profile_min_samples` samples of its
    own; the best rate wins, ties go to more samples, then to the name."""
    turns = sorted(inputs.turns, key=lambda t: t.seq)
    t1_by_skill: dict[str, list[EvidenceSample]] = {}
    for e in inputs.evidence:
        if e.tier == 1:
            t1_by_skill.setdefault(e.skill_id, []).append(e)
    # The turns aimed at each skill, in order, to find the next one.
    aimed: dict[str, list[int]] = {}
    for t in turns:
        for skill in t.target_skill_ids:
            aimed.setdefault(skill, []).append(t.seq)
    samples: dict[str, list[bool]] = {}
    for t in turns:
        if t.approach == "none":
            continue
        for skill in t.target_skill_ids:
            later = [s for s in aimed[skill] if s > t.seq]
            end = later[0] if later else None
            attempts = [
                e
                for e in t1_by_skill.get(skill, [])
                if e.source_seq > t.seq and (end is None or e.source_seq < end)
            ]
            if not attempts:
                continue
            first = attempts[0]  # evidence is ordered by source_seq
            samples.setdefault(t.approach, []).append(
                first.outcome == "correct" and first.assistance == "none"
            )
    best: tuple[float, int, str] | None = None
    for approach, results in samples.items():
        if len(results) < params.profile_min_samples:
            continue
        rate = sum(results) / len(results)
        key = (rate, len(results), approach)
        # Higher rate, more samples, then the alphabetically first name.
        if best is None or key[:2] > best[:2] or (key[:2] == best[:2] and approach < best[2]):
            best = key
    return best[2] if best else None


def _calibration(
    inputs: ProfileInputs, params: ProfileParams
) -> tuple[float | None, Literal["over", "under", "calibrated"] | None]:
    """Brier score and the sign of the bias of the self-assessments against the outcomes of
    evidence that passes the gate, over the last `calibration_window` reports (Т-§9.5). A
    report is paired with the gate-passing `correct`/`incorrect` evidence of its source; it
    counts as success only if all of them are correct. `partial` never opens the gate."""
    by_source: dict[int, list[bool]] = {}
    for e in inputs.evidence:
        if e.passes_gate and e.outcome in ("correct", "incorrect"):
            by_source.setdefault(e.source_seq, []).append(e.outcome == "correct")
    pairs: list[tuple[float, float]] = []
    for c in inputs.confidences:  # ordered by target_seq
        outcomes = by_source.get(c.target_seq)
        probability = CONFIDENCE_PROBABILITY.get(c.value)
        if not outcomes or probability is None:
            continue
        pairs.append((probability, 1.0 if all(outcomes) else 0.0))
    pairs = pairs[-params.calibration_window :]
    if len(pairs) < max(1, params.calibration_min_reports):
        return None, None
    n = len(pairs)
    brier = round(sum((p - o) ** 2 for p, o in pairs) / n, _BRIER_DIGITS)
    skew = sum(p - o for p, o in pairs) / n
    if skew > _BIAS_EPS:
        return brier, "over"
    if skew < -_BIAS_EPS:
        return brier, "under"
    return brier, "calibrated"


def fold_profile(inputs: ProfileInputs, params: ProfileParams) -> ProfileFold:
    """The learned part of the profile: deterministic in the inputs (Т-§9.5, Т-§2.5)."""
    brier, bias = _calibration(inputs, params)
    branch_rate: float | None = None
    if inputs.turns and len(inputs.turns) >= max(1, params.profile_min_samples):
        corrective = sum(1 for t in inputs.turns if t.branch_type == "corrective")
        branch_rate = round(corrective / len(inputs.turns), _BRIER_DIGITS)
    reasoning_share: float | None = None
    if inputs.answers and len(inputs.answers) >= max(1, params.profile_min_samples):
        reasoned = sum(1 for a in inputs.answers if a.reasoned)
        reasoning_share = round(reasoned / len(inputs.answers), _BRIER_DIGITS)
    return ProfileFold(
        helpful_approach=_helpful_approach(inputs, params),
        confidence_brier=brier,
        confidence_bias=bias,
        branch_rate=branch_rate,
        reasoning_share=reasoning_share,
    )


def profile_node_props(
    inputs: ProfileInputs, params: ProfileParams
) -> tuple[Props, tuple[str, ...]]:
    """Everything the projector writes to `Profile` besides `learnerId`: `pref*`,
    `learnedManner`, the present `learned*`; and the `learned*` keys to remove (no data now).
    Phase B writes the node whenever the profile is dirty (an event of `PROFILE_EVENT_TYPES` or
    a change of the learner's evidence)."""
    fold = fold_profile(inputs, params)
    props: Props = dict(inputs.prefs)
    if inputs.manner is not None:
        props["learnedManner"] = inputs.manner
    props.update(fold.to_props())
    return props, fold.unset_keys()
