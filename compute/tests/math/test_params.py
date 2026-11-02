"""ТЗ Ф4 §0.3: thresholds of the math core come from the caller's `Settings` through `Params`,
not from module constants (D-B-124)."""

from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from quack_compute.math.equivalence import Equivalent, Params, compare

MATH_DIR = Path(__file__).resolve().parents[2] / "quack_compute" / "math"

PARAMS = Params(
    numeric_points=20,
    simplify_budget_ms=2000,
    precision_digits=30,
    sample_low=-10.0,
    sample_high=10.0,
    positive_sample_min=1e-3,
    max_attempts_per_point=10,
    rel_tol=1e-9,
    abs_tol=1e-12,
)


def _numeric_constants(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.Assign | ast.AnnAssign) or node.value is None:
            continue
        values = node.value.elts if isinstance(node.value, ast.Tuple) else [node.value]
        if not any(
            isinstance(v, ast.Constant)
            and isinstance(v.value, int | float)
            and not isinstance(v.value, bool)
            for v in values
        ):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            names = target.elts if isinstance(target, ast.Tuple) else [target]
            found.extend(n.id for n in names if isinstance(n, ast.Name))
    return found


def test_no_numeric_module_constants_in_math() -> None:
    offenders = {
        path.name: names
        for path in sorted(MATH_DIR.glob("*.py"))
        if (names := _numeric_constants(path))
    }
    assert offenders == {}, "move to Settings and pass through Params (§0.3)"


def test_params_carry_every_threshold() -> None:
    assert {f.name for f in dataclasses.fields(Params)} == {
        "numeric_points",
        "simplify_budget_ms",
        "precision_digits",
        "sample_low",
        "sample_high",
        "positive_sample_min",
        "max_attempts_per_point",
        "rel_tol",
        "abs_tol",
    }


def test_tolerance_comes_from_params() -> None:
    strict = compare("x + 0.000001", "x", params=PARAMS)
    loose = compare(
        "x + 0.000001", "x", params=dataclasses.replace(PARAMS, rel_tol=1e-3, abs_tol=1e-3)
    )
    assert isinstance(strict, Equivalent) and not strict.equivalent
    assert isinstance(loose, Equivalent) and loose.equivalent
