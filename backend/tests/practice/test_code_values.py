"""Т-§6.10: comparing tagged values and SQL rows (no database, no runner)."""

from __future__ import annotations

import pytest

from quack.practice.code_values import (
    rows_equal,
    sorts_at_top_level,
    unordered_equal,
    values_equal,
)

TOL = 1e-9


@pytest.mark.parametrize(
    ("expected", "actual", "same"),
    [
        (3, 3, True),
        (3, 3.0, False),
        (True, 1, False),
        (None, None, True),
        ("a", "a", True),
        (0.1 + 0.2, 0.3, True),
        (1.0, 1.0001, False),
        ({"$float": "nan"}, {"$float": "nan"}, True),
        ({"$float": "inf"}, {"$float": "-inf"}, False),
        ([1, [2, 3]], [1, [2, 3]], True),
        ([1, 2], {"$tuple": [1, 2]}, False),
        ({"$set": [1, 2, 3]}, {"$set": [3, 1, 2]}, True),
        ({"$dict": [["a", 1], [2, "b"]]}, {"$dict": [[2, "b"], ["a", 1]]}, True),
        ({"$dict": [["a", 1]]}, {"$dict": [["a", 2]]}, False),
        ({"$dict": [[1, "x"]]}, {"$dict": [["1", "x"]]}, False),
    ],
)
def test_values_equal(expected: object, actual: object, same: bool) -> None:
    assert values_equal(expected, actual, tolerance=TOL) is same


def test_float_tol_mixes_int_and_float() -> None:
    assert values_equal([2, 0.5], [2.0, 0.5], tolerance=TOL, mixed_numbers=True)
    assert not values_equal([2], [2.0], tolerance=TOL)


def test_unordered_counts_duplicates() -> None:
    assert unordered_equal([1, 2, 2], [2, 1, 2], tolerance=TOL)
    assert not unordered_equal([1, 2, 2], [1, 1, 2], tolerance=TOL)


def test_rows_without_order() -> None:
    expected = [["Ann", 30], ["Bob", 25]]
    assert rows_equal(
        expected, [["Bob", 25], ["Ann", 30]], ordered=False, tolerance=TOL, sort_digits=8
    )
    assert not rows_equal(
        expected, [["Bob", 25], ["Ann", 30]], ordered=True, tolerance=TOL, sort_digits=8
    )
    assert not rows_equal(expected, [["Ann", 30]], ordered=False, tolerance=TOL, sort_digits=8)


def test_sql_cells() -> None:
    assert rows_equal([[1, None]], [[1.0, None]], ordered=True, tolerance=TOL, sort_digits=8)
    assert not rows_equal([[1]], [["1"]], ordered=True, tolerance=TOL, sort_digits=8)
    assert rows_equal([[2 / 3]], [[0.6666666666666666]], ordered=True, tolerance=TOL, sort_digits=8)


@pytest.mark.parametrize(
    ("query", "sorts"),
    [
        ("SELECT name FROM people ORDER BY age", True),
        ("select name from people order  by age desc", True),
        ("SELECT name, row_number() OVER (ORDER BY age) FROM people", False),
        ("SELECT * FROM (SELECT * FROM t ORDER BY x) LIMIT 3", False),
        ("SELECT 'ORDER BY' AS s FROM t", False),
        ("SELECT x FROM t -- ORDER BY x", False),
    ],
)
def test_sorts_at_top_level(query: str, sorts: bool) -> None:
    assert sorts_at_top_level(query) is sorts
