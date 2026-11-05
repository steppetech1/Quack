"""Quality of the extraction on the reference set (ТЗ Ф4 WP16.7; Т-§7.11, Т-§18.1, Т-§18.3).
D-B-209.

The reference set is labelled episodes (`EvalSet`, a JSON file the operator exports from
LangSmith, Т-§18.1; learners without the `research` consent do not get into it, Т-§19.4). Each
episode carries the **input of the extractor as it was rendered** — the five blocks of
`extractor@3` with their labels — and the expected elements by the same labels. The extractor
runs on it offline `extraction_eval_runs` times per episode through the gateway; nothing is
written to the log of a learner (no `observation.extracted`, no coverage row; the model calls
go to the ledger of the gateway, as in the replay).

Metrics (each is `{"status": "measured", ...}` or `{"status": "not_measured", "reason"}` —
no data is «not measured», never a zero, law 8):

- **precision** of negative elements and misconception hypotheses (the main one, Т-§7.11):
  of the elements the code accepted — a failed or partial evidence, a hypothesis — the share
  that match an expected element; pooled over all runs. Recall is secondary, reported beside.
- **kappa** (Cohen) between runs, per field and the mean of the fields (`kappa`).
- **code rejections**: the share of elements the check by code rejected (Т-§18.3).

`compare_reports(old, new)` is the gate of Т-§18.4 for the extractor: a new prompt version may
lower neither the precision nor the consistency.
"""

from __future__ import annotations

import asyncio
import hashlib
import itertools
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid5

import orjson
from pydantic import BaseModel, ConfigDict, Field

from quack.agents.extract_episode import EXTRACTOR_ROLE, unknown_label_errors
from quack.agents.metrics import not_measured
from quack.agents.schemas import ExtractedElement, ExtractorOutputV3
from quack.config import Settings
from quack.graph.evidence_validation import EpisodeCheckContext, EpisodeSource, check_extracted
from quack.llm.gateway import LLMGateway

type Metric = dict[str, Any]
type Level = Annotated[int, Field(ge=1, le=6)]

NEGATIVE_OUTCOMES = ("incorrect", "partial")
KAPPA_FIELDS = ("presence", "level", "outcome", "help", "gap_type", "misconception")
_NAMESPACE = UUID("00000000-0000-0000-0000-000000000b16")
_NEW = "<new>"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExpectedElement(_Strict):
    """An expected element. `skill` — the label of the episode's candidate table, `None` for a
    new or unclear skill; `level` — `None` matches any level; `outcome` — evidence only;
    `misconception` — the label of a known one, `None` for a hypothesis of a new statement."""

    kind: Literal["evidence", "misconception_hypothesis"]
    skill: str | None = None
    level: Level | None = None
    outcome: Literal["correct", "incorrect", "partial"] | None = None
    misconception: str | None = None


class EvalSource(_Strict):
    """An event of the episode that an element may cite (`EpisodeSource`)."""

    seq: int
    text: str
    at_ms: int = 0


class EvalCheck(_Strict):
    """What the check by code knows about the episode (`EpisodeCheckContext`, labels instead
    of ids). Without it the episode is not checked and its rejections are not measured."""

    skill_labels: list[str]
    misconception_labels: list[str] = Field(default_factory=list)
    episode: list[EvalSource]
    task_levels: dict[int, int] = Field(default_factory=dict)
    code_verdicts: list[tuple[str, int]] = Field(default_factory=list)


class EvalEpisode(_Strict):
    """`variables` — the blocks of `extractor@3` (`events_before`, `episode`,
    `candidate_skills`, `known_misconceptions`, `gap_types`) with the labels `expected` uses."""

    episode_id: str
    variables: dict[str, str]
    expected: list[ExpectedElement]
    check: EvalCheck | None = None


class EvalSet(_Strict):
    name: str
    episodes: list[EvalEpisode]


def load_eval_set(path: Path) -> EvalSet:
    """The reference set from the JSON file the operator exported."""
    return EvalSet.model_validate(orjson.loads(path.read_bytes()))


def _label_id(kind: str, label: str) -> UUID:
    return uuid5(_NAMESPACE, f"{kind}:{label}")


def check_context(check: EvalCheck) -> EpisodeCheckContext:
    return EpisodeCheckContext(
        skill_labels={label: _label_id("skill", label) for label in check.skill_labels},
        misconception_labels={
            label: _label_id("misconception", label) for label in check.misconception_labels
        },
        episode={s.seq: EpisodeSource(s.seq, s.text, s.at_ms) for s in check.episode},
        task_levels=dict(check.task_levels),
        code_verdicts=frozenset(
            (_label_id("skill", label), level) for label, level in check.code_verdicts
        ),
    )


# --- matching (D-B-209) ---


def is_negative(element: ExtractedElement | ExpectedElement) -> bool:
    """The elements the precision is about: a failed or partial evidence and any hypothesis."""
    return element.kind == "misconception_hypothesis" or element.outcome in NEGATIVE_OUTCOMES


def matches(got: ExtractedElement, expected: ExpectedElement) -> bool:
    """D-B-209: the same kind and skill (a new or unclear skill matches `skill: None`); evidence
    — the same polarity of the outcome (negative or correct) and, when the expected level is
    given, the same level; a hypothesis — the same known misconception, or both new (the text
    of a new statement is not compared)."""
    if got.kind != expected.kind or got.skill != expected.skill:
        return False
    if got.kind == "misconception_hypothesis":
        return got.misconception == expected.misconception
    if (got.outcome in NEGATIVE_OUTCOMES) != (expected.outcome in NEGATIVE_OUTCOMES):
        return False
    return expected.level is None or got.level == expected.level


def match_elements(
    got: Sequence[ExtractedElement], expected: Sequence[ExpectedElement]
) -> list[int | None]:
    """For each obtained element the index of the expected one it matches, one to one in the
    order of the output; `None` — no match."""
    used: set[int] = set()
    result: list[int | None] = []
    for element in got:
        found = next(
            (i for i, e in enumerate(expected) if i not in used and matches(element, e)), None
        )
        if found is not None:
            used.add(found)
        result.append(found)
    return result


# --- consistency (D-B-209) ---


def _misconception_key(element: ExtractedElement) -> str | None:
    if element.misconception is not None:
        return element.misconception
    return _NEW if element.new_misconception is not None else None


def _units(run: Sequence[ExtractedElement]) -> dict[tuple[str, str | None, int], ExtractedElement]:
    """The elements of one run by unit: kind, skill and the number among its namesakes."""
    seen: Counter[tuple[str, str | None]] = Counter()
    units: dict[tuple[str, str | None, int], ExtractedElement] = {}
    for element in run:
        base = (element.kind, element.skill)
        units[(*base, seen[base])] = element
        seen[base] += 1
    return units


def _field(element: ExtractedElement, name: str) -> str:
    value = _misconception_key(element) if name == "misconception" else getattr(element, name)
    return str(value)


def kappa_pairs(runs: Sequence[Sequence[ExtractedElement]]) -> dict[str, list[tuple[str, str]]]:
    """Pairs of values of two runs per field, for every ordered pair of runs of an episode.
    `presence` — over all units of all runs (`absent` where a run lacks it); the other fields —
    over the units both runs have."""
    units = [_units(run) for run in runs]
    keys = sorted({k for u in units for k in u}, key=repr)
    pairs: dict[str, list[tuple[str, str]]] = {name: [] for name in KAPPA_FIELDS}
    for i, j in itertools.permutations(range(len(runs)), 2):
        for key in keys:
            a, b = units[i].get(key), units[j].get(key)
            pairs["presence"].append(
                ("absent" if a is None else "present", "absent" if b is None else "present")
            )
            if a is not None and b is not None:
                for name in KAPPA_FIELDS[1:]:
                    pairs[name].append((_field(a, name), _field(b, name)))
    return pairs


def cohen_kappa(pairs: Sequence[tuple[str, str]]) -> float | None:
    """Cohen's kappa of two raters over the pairs; `None` without pairs. Total agreement on one
    category (chance agreement 1) is 1.0."""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(a == b for a, b in pairs) / n
    first = Counter(a for a, _ in pairs)
    second = Counter(b for _, b in pairs)
    pe = sum(first[c] * second[c] for c in first) / (n * n)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


# --- the run ---


def _kappa_metric(pairs: dict[str, list[tuple[str, str]]], runs: int) -> Metric:
    if runs < 2:
        return not_measured("fewer than two runs per episode")
    fields: dict[str, Metric] = {}
    for name in KAPPA_FIELDS:
        value = cohen_kappa(pairs[name])
        fields[name] = (
            not_measured("no pairs of runs")
            if value is None
            else {"status": "measured", "value": value, "count": len(pairs[name])}
        )
    measured = [m["value"] for m in fields.values() if m["status"] == "measured"]
    if not measured:
        return not_measured("no elements in any run")
    return {
        "status": "measured",
        "value": sum(measured) / len(measured),
        "count": len(measured),
        "of": len(KAPPA_FIELDS),
        "fields": fields,
    }


def _share(count: int, total: int, reason: str) -> Metric:
    if total == 0:
        return not_measured(reason)
    return {"status": "measured", "value": count / total, "count": count, "of": total}


def _fingerprint(eval_set: EvalSet) -> str:
    return hashlib.sha256(eval_set.model_dump_json().encode()).hexdigest()[:16]


async def _run_once(
    gateway: LLMGateway,
    episode: EvalEpisode,
    *,
    prompt: str,
    learner_id: UUID,
    key: str,
) -> ExtractorOutputV3:
    check = episode.check
    skills = set(check.skill_labels) if check else None
    known = set(check.misconception_labels) if check else set()
    result = await gateway.generate_structured(
        EXTRACTOR_ROLE,
        int(prompt.rsplit("@", 1)[1]),
        ExtractorOutputV3,
        episode.variables,
        learner_id,
        None,
        None,
        key,
        check=None if skills is None else lambda o: unknown_label_errors(o, skills, known),
    )
    return result.value


async def evaluate_extraction(
    gateway: LLMGateway,
    settings: Settings,
    eval_set: EvalSet,
    *,
    learner_id: UUID,
    prompt: str | None = None,
    runs: int | None = None,
    out: Path | None = None,
) -> dict[str, Any]:
    """Run the extractor on every episode of the set `runs` times (default
    `settings.extraction_eval_runs`) with `prompt` (`extractor@N`, default the role's) and
    return the report; `out` — also write it as JSON. `learner_id` — whose ledger the model
    calls go to (the operator's evaluation learner). The key of a call carries the prompt, the
    episode, the run and the hash of its input: a repeat does not pay twice (law 5), an edited
    episode is run again. An episode whose run fails is in the report with its error."""
    prompt = prompt or settings.llm_roles[EXTRACTOR_ROLE].prompt
    if prompt is None:
        raise ValueError(f"role {EXTRACTOR_ROLE!r} has no prompt")
    count = settings.extraction_eval_runs if runs is None else runs
    if count < 1:
        raise ValueError("runs must be at least 1")

    matched = {"negative": 0, "hypothesis": 0}
    accepted = {"negative": 0, "hypothesis": 0}
    expected_negative = hit_negative = 0
    rejected = checked_total = 0
    pairs: dict[str, list[tuple[str, str]]] = {name: [] for name in KAPPA_FIELDS}
    episodes: list[dict[str, Any]] = []
    for episode in eval_set.episodes:
        digest = hashlib.sha256(orjson.dumps(episode.variables)).hexdigest()[:12]
        try:
            outputs = [
                await _run_once(
                    gateway,
                    episode,
                    prompt=prompt,
                    learner_id=learner_id,
                    key=f"eval:{prompt}:{episode.episode_id}:{digest}:{n}",
                )
                for n in range(count)
            ]
        except Exception as exc:  # one failed episode does not stop the set (law 7)
            episodes.append({"episode_id": episode.episode_id, "error": repr(exc)})
            continue
        runs_elements = [list(o.elements) for o in outputs]
        for name, found in kappa_pairs(runs_elements).items():
            pairs[name].extend(found)
        context = None if episode.check is None else check_context(episode.check)
        expected_negative += count * sum(is_negative(e) for e in episode.expected)
        rejections = 0
        for elements in runs_elements:
            bad: set[int] = set()
            if context is not None:
                bad = {r.index for r in check_extracted(elements, context).rejected}
                checked_total += len(elements)
                rejections += len(bad)
            kept = [e for i, e in enumerate(elements) if i not in bad]
            for element, hit in zip(kept, match_elements(kept, episode.expected), strict=True):
                if not is_negative(element):
                    continue
                part = "hypothesis" if element.kind == "misconception_hypothesis" else "negative"
                accepted[part] += 1
                matched[part] += hit is not None
                hit_negative += hit is not None
        rejected += rejections
        episodes.append(
            {
                "episode_id": episode.episode_id,
                "error": None,
                "elements_per_run": [len(e) for e in runs_elements],
                "rejected": rejections if context is not None else None,
            }
        )

    ok = [e for e in episodes if e["error"] is None]
    total_accepted = sum(accepted.values())
    report: dict[str, Any] = {
        "dataset": eval_set.name,
        "fingerprint": _fingerprint(eval_set),
        "prompt": prompt,
        "runs": count,
        "episodes_total": len(eval_set.episodes),
        "episodes_run": len(ok),
        "precision": _share(
            sum(matched.values()), total_accepted, "no negative elements or hypotheses accepted"
        ),
        "precision_evidence": _share(
            matched["negative"], accepted["negative"], "no negative evidence accepted"
        ),
        "precision_hypothesis": _share(
            matched["hypothesis"], accepted["hypothesis"], "no hypotheses accepted"
        ),
        "recall": _share(hit_negative, expected_negative, "no negative elements expected"),
        "kappa": _kappa_metric(pairs, count) if ok else not_measured("no episode was run"),
        "code_rejections": _share(rejected, checked_total, "no episode has a check context"),
        "episodes": episodes,
        "method": "D-B-209",
    }
    if out is not None:
        await asyncio.to_thread(out.write_bytes, orjson.dumps(report, option=orjson.OPT_INDENT_2))
    return report


# --- comparison of versions (the gate of Т-§18.4) ---

GATED = ("precision", "kappa")


def compare_reports(
    old: dict[str, Any], new: dict[str, Any], *, tolerance: float = 0.0
) -> dict[str, Any]:
    """Т-§7.11: a new version may lower neither the precision of negative elements and
    hypotheses nor the consistency. `regression` is `True` when a gated metric of `new` is
    lower than `old` by more than `tolerance`, `False` when at least one was compared and none
    fell, `None` when nothing could be compared (a metric not measured on either side, or the
    sets differ: not «passed», law 8). Rejections and recall are shown, not gated."""
    same = old.get("fingerprint") == new.get("fingerprint")
    metrics: dict[str, Any] = {}
    for name in (*GATED, "recall", "code_rejections"):
        a, b = old.get(name, {}), new.get(name, {})
        if a.get("status") != "measured" or b.get("status") != "measured":
            metrics[name] = {"status": "not_compared", "dropped": None}
            continue
        delta = b["value"] - a["value"]
        metrics[name] = {
            "status": "compared",
            "old": a["value"],
            "new": b["value"],
            "delta": delta,
            "dropped": delta < -tolerance if name in GATED else None,
        }
    gated = [metrics[n]["dropped"] for n in GATED if metrics[n]["dropped"] is not None]
    regression: bool | None = None if not same or not gated else any(gated)
    return {
        "old_prompt": old.get("prompt"),
        "new_prompt": new.get("prompt"),
        "same_dataset": same,
        "metrics": metrics,
        "regression": regression,
    }
