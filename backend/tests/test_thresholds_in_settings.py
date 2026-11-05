"""WP1.6: thresholds of Phase 2 live in `Settings` under a name, not as module constants
(ТЗ Фазы 3 §0.3, §6.2, audit A7). Values are unchanged; behaviour is covered by the existing
tests of the streams, so these tests pin the diagnostic defaults (they go with the diagnostic,
backend/TESTS.md) and the absence of constants. The pins of the other defaults and the check
that `session/deps.py` has no RATE_LIMIT constant were removed (D-C-146): the consumers' tests
take the limits from `Settings`.
"""

from __future__ import annotations

import ast
from pathlib import Path

from quack.config import Settings

QUACK_DIR = Path(__file__).resolve().parent.parent / "quack"

# Zone of stream C where a numeric module constant is a threshold. Not scanned:
# `artifacts/practice.py` and `artifacts/params.py` belong to the practice move of stream B
# (D-C-013); `graph/` holds mathematical and unit constants of the folds.
SCANNED = ("domains", "artifacts", "api")
NOT_SCANNED = {"artifacts/practice.py", "artifacts/params.py", "api/ratelimit.py"}


def test_diagnostic_parameters_have_phase_2_values() -> None:
    diagnostic = Settings().diagnostic
    assert diagnostic.max_questions == 5
    assert diagnostic.prior_tested_max_mastery == 0.5
    assert diagnostic.prior_tested_factor == 0.5
    assert diagnostic.prior_tested_confidence == 0.5
    assert diagnostic.prior_propagated_success_threshold == 0.7
    assert diagnostic.prior_propagated_fail_threshold == 0.5
    assert diagnostic.prior_propagated_decay == 0.85
    assert diagnostic.prior_down_base_mastery == 0.4
    assert diagnostic.prior_down_base_confidence == 0.35
    assert diagnostic.prior_up_fail_mastery == 0.05
    assert diagnostic.prior_up_base_confidence == 0.4
    assert diagnostic.self_report_correct_threshold == 0.6
    assert diagnostic.no_self_report_score == 0.5
    assert diagnostic.default_tested_depth == 0.5


def _numeric_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign | ast.AnnAssign):
            continue
        value = node.value
        if not (
            isinstance(value, ast.Constant)
            and isinstance(value.value, int | float)
            and not isinstance(value.value, bool)
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        found.extend(t.id for t in targets if isinstance(t, ast.Name) and t.id.isupper())
    return found


def test_no_numeric_threshold_constants_left_in_stream_c_modules() -> None:
    offenders: dict[str, list[str]] = {}
    for package in SCANNED:
        for path in sorted((QUACK_DIR / package).rglob("*.py")):
            relative = path.relative_to(QUACK_DIR).as_posix()
            if relative in NOT_SCANNED:
                continue
            if names := _numeric_constants(path):
                offenders[relative] = names
    assert offenders == {}, "move to Settings under a named parameter (WP1.6)"
