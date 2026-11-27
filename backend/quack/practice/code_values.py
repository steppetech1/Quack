"""Comparing what learner code gave with what a test expects (Т-§6.10; «Значения и сравнение»).

Values are tagged JSON, as the runner reports them: null, bool, int, float, str and list as
they are; `{"$float": "nan"|"inf"|"-inf"}`, `{"$tuple": [...]}`, `{"$set": [...]}`,
`{"$dict": [[key, value], ...]}`, `{"$bytes": hex}` (SQL). Comparison is strict about types:
`True` is not `1`, a list is not a tuple, `1` is not `1.0` unless the test says `float_tol`.
Floats are equal within the relative and absolute `tolerance`.
"""

from __future__ import annotations

import math
import re
from typing import Any

_TAGS = ("$float", "$tuple", "$set", "$dict", "$bytes")


def _tag(value: Any) -> str | None:
    if isinstance(value, dict) and len(value) == 1:
        (key,) = value
        if key in _TAGS:
            return key
    return None


def _number(value: Any) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if _tag(value) == "$float":
        return float(value["$float"])
    return None


def _floats_equal(a: float, b: float, tolerance: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return math.isnan(a) and math.isnan(b)
    if math.isinf(a) or math.isinf(b):
        return a == b
    return math.isclose(a, b, rel_tol=tolerance, abs_tol=tolerance)


def values_equal(
    expected: Any, actual: Any, *, tolerance: float, mixed_numbers: bool = False
) -> bool:
    """Equal tagged values. `mixed_numbers` — an int and a float with the same value are equal."""
    exp_num, act_num = _number(expected), _number(actual)
    if exp_num is not None or act_num is not None:
        if exp_num is None or act_num is None:
            return False
        exp_float = isinstance(exp_num, float)
        act_float = isinstance(act_num, float)
        if exp_float != act_float and not mixed_numbers:
            return False
        if not exp_float and not act_float:
            return exp_num == act_num
        return _floats_equal(float(exp_num), float(act_num), tolerance)
    if expected is None or isinstance(expected, bool | str):
        return type(expected) is type(actual) and expected == actual
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(
                values_equal(e, a, tolerance=tolerance, mixed_numbers=mixed_numbers)
                for e, a in zip(expected, actual, strict=True)
            )
        )
    tag = _tag(expected)
    if tag is None or tag != _tag(actual):
        return False
    body, other = expected[tag], actual[tag]
    if tag == "$bytes":
        return body == other
    if tag == "$tuple":
        return values_equal(body, other, tolerance=tolerance, mixed_numbers=mixed_numbers)
    if tag == "$set":
        return _multiset_equal(body, other, tolerance=tolerance, mixed_numbers=mixed_numbers)
    # $dict: same keys (exactly), equal values.
    if not isinstance(body, list) or not isinstance(other, list) or len(body) != len(other):
        return False
    remaining = list(other)
    for key, value in body:
        index = next(
            (
                i
                for i, (k, _) in enumerate(remaining)
                if values_equal(key, k, tolerance=0.0, mixed_numbers=False)
            ),
            None,
        )
        if index is None:
            return False
        _, other_value = remaining.pop(index)
        if not values_equal(value, other_value, tolerance=tolerance, mixed_numbers=mixed_numbers):
            return False
    return True


def _multiset_equal(
    expected: list[Any], actual: list[Any], *, tolerance: float, mixed_numbers: bool
) -> bool:
    if not isinstance(expected, list) or not isinstance(actual, list):
        return False
    if len(expected) != len(actual):
        return False
    remaining = list(actual)
    for item in expected:
        index = next(
            (
                i
                for i, other in enumerate(remaining)
                if values_equal(item, other, tolerance=tolerance, mixed_numbers=mixed_numbers)
            ),
            None,
        )
        if index is None:
            return False
        remaining.pop(index)
    return True


def unordered_equal(expected: Any, actual: Any, *, tolerance: float) -> bool:
    """`unordered`: two lists as multisets."""
    if not isinstance(expected, list) or not isinstance(actual, list):
        return False
    return _multiset_equal(expected, actual, tolerance=tolerance, mixed_numbers=False)


# --- SQL ---------------------------------------------------------------------------------------


def _sql_cell_equal(expected: Any, actual: Any, tolerance: float) -> bool:
    """SQLite cells: INTEGER and REAL equal by value; text is not a number; NULL equals NULL."""
    return values_equal(expected, actual, tolerance=tolerance, mixed_numbers=True)


def _row_key(row: list[Any], digits: int) -> str:
    """A sort key of a row: floats rounded, so rows equal within the tolerance sort together."""
    parts = []
    for cell in row:
        number = _number(cell)
        if number is not None and not (isinstance(number, float) and not math.isfinite(number)):
            parts.append(f"n:{float(number):.{digits}g}")
        else:
            parts.append(f"v:{cell!r}")
    return "|".join(parts)


def rows_equal(
    expected: list[list[Any]],
    actual: list[list[Any]],
    *,
    ordered: bool,
    tolerance: float,
    sort_digits: int,
) -> bool:
    """The rows of a query against the rows of the reference: same width, same rows; the order
    counts only when the reference query sorts (`ordered`). Duplicates count."""
    if len(expected) != len(actual):
        return False
    if any(len(row) != len(expected[0]) for row in [*expected, *actual]):
        return False
    if not ordered:
        expected = sorted(expected, key=lambda row: _row_key(row, sort_digits))
        actual = sorted(actual, key=lambda row: _row_key(row, sort_digits))
    return all(
        all(_sql_cell_equal(e, a, tolerance) for e, a in zip(exp_row, act_row, strict=True))
        for exp_row, act_row in zip(expected, actual, strict=True)
    )


_SQL_LITERALS = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/", re.S)
_ORDER_BY = re.compile(r"\border\s+by\b", re.I)


def sorts_at_top_level(query: str) -> bool:
    """The query has `ORDER BY` outside any parentheses (window functions and subqueries do not
    count), so the order of its rows is part of the answer."""
    text = _SQL_LITERALS.sub(" ", query)
    depth = 0
    top = []
    for char in text:
        if char == "(":
            depth += 1
            top.append(" ")
        elif char == ")":
            depth = max(0, depth - 1)
            top.append(" ")
        else:
            top.append(char if depth == 0 else " ")
    return _ORDER_BY.search("".join(top)) is not None
