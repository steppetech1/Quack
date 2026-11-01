"""WP0 п.3в (ТЗ Фазы 4): parameter names of the pinned py-fsrs scheduler (`Т-§8.3`, «Ориентир»).

Checks on the installed `fsrs` package: the scheduler accepts `enable_fuzzing`,
`learning_steps`, `relearning_steps`, `desired_retention`; the default parameter set has 21
weights (FSRS-6); with fuzzing off and empty steps the same reviews at the same UTC times give
the same due dates (determinism, `Т-§8.3`); a first `Good` puts the card into `Review` (no
learning steps). Prints one JSON line; exit 0 — as expected, 1 — not.

Not part of `pytest` (fsrs is added to the backend with WP4). Run from `backend/`:

    uv run --with fsrs==6.3.2 python scripts/checks/fsrs_params.py
"""

from __future__ import annotations

import inspect
import json
import sys
from datetime import UTC, datetime, timedelta
from importlib.metadata import version

EXPECTED = ("enable_fuzzing", "learning_steps", "relearning_steps", "desired_retention")


def main() -> int:
    import fsrs  # imported here so the script reports a missing package as a result

    report: dict[str, object] = {"check": "WP0-3v py-fsrs scheduler parameters"}
    report["fsrs_version"] = version("fsrs")
    names = list(inspect.signature(fsrs.Scheduler.__init__).parameters)
    report["scheduler_parameters"] = names
    report["missing"] = [n for n in EXPECTED if n not in names]
    defaults = fsrs.Scheduler().parameters
    report["default_weights"] = len(defaults)

    def run() -> list[str]:
        scheduler = fsrs.Scheduler(
            enable_fuzzing=False, learning_steps=(), relearning_steps=(), desired_retention=0.9
        )
        card = fsrs.Card()
        at = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
        dues: list[str] = []
        for rating, gap_days in (
            (fsrs.Rating.Good, 0),
            (fsrs.Rating.Good, 3),
            (fsrs.Rating.Again, 10),
            (fsrs.Rating.Hard, 2),
        ):
            at += timedelta(days=gap_days)
            card, _ = scheduler.review_card(card, rating, review_datetime=at)
            dues.append(f"{card.state.name}:{card.due.isoformat()}")
        return dues

    first, second = run(), run()
    report["reviews"] = first
    report["deterministic"] = first == second
    ok = (
        not report["missing"]
        and len(defaults) == 21
        and first == second
        and first[0].startswith("Review:")
    )
    report["works"] = ok
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
