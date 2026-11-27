"""SQLite runs: read-only queries on a fresh in-memory database per test."""

from __future__ import annotations

import pytest

SCHEMA = "CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, age INTEGER);"
DATA_A = "INSERT INTO people VALUES (1, 'Ann', 30), (2, 'Bob', 25);"
DATA_B = "INSERT INTO people VALUES (1, 'Cid', 41);"


async def test_query_per_dataset(sql) -> None:
    results = await sql(SCHEMA, "SELECT name, age FROM people ORDER BY id", DATA_A, DATA_B)
    assert [r.status for r in results] == ["ok", "ok"]
    assert results[0].columns == ["name", "age"]
    assert results[0].rows == [["Ann", 30], ["Bob", 25]]
    assert results[1].rows == [["Cid", 41]]


async def test_recursive_cte_allowed(sql) -> None:
    query = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 3) "
        "SELECT x FROM n"
    )
    (result,) = await sql(SCHEMA, query, DATA_A)
    assert result.rows == [[1], [2], [3]]


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM people",
        "INSERT INTO people VALUES (9, 'Eve', 1)",
        "UPDATE people SET age = 0",
        "DROP TABLE people",
        "CREATE TABLE t (x)",
        "PRAGMA table_info(people)",
        "ATTACH DATABASE '/etc/passwd' AS p",
        "SELECT load_extension('x')",
        "BEGIN",
    ],
)
async def test_only_reads(sql, query) -> None:
    (result,) = await sql(SCHEMA, query, DATA_A)
    assert result.status == "error"


async def test_one_statement(sql) -> None:
    (result,) = await sql(SCHEMA, "SELECT 1; SELECT 2", DATA_A)
    assert result.status == "error"


async def test_endless_recursion_times_out(sql) -> None:
    query = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT count(*) FROM n"
    (result,) = await sql(SCHEMA, query, DATA_A)
    assert result.status == "timeout"


async def test_row_limit(sql, settings) -> None:
    query = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n "
        f"WHERE x < {settings.sql_max_rows + 5}) SELECT x FROM n"
    )
    (result,) = await sql(SCHEMA, query, DATA_A)
    assert result.status == "row_limit"


async def test_data_is_fresh_per_test(sql) -> None:
    results = await sql(SCHEMA, "SELECT count(*) FROM people", DATA_A, DATA_A)
    assert [r.rows for r in results] == [[[2]], [[2]]]


async def test_broken_data_script_is_bad_input(sql) -> None:
    (result,) = await sql(SCHEMA, "SELECT 1", "INSERT INTO nowhere VALUES (1);")
    assert result.status == "bad_input"


async def test_values(sql) -> None:
    (result,) = await sql(SCHEMA, "SELECT NULL, 1.5, x'ff', 9e999", DATA_A)
    assert result.rows == [[None, 1.5, {"$bytes": "ff"}, {"$float": "inf"}]]


async def test_http_contract(client) -> None:
    response = await client.post(
        "/v1/run/sql",
        json={
            "schema_sql": SCHEMA,
            "query": "SELECT name FROM people",
            "tests": [{"data_sql": DATA_B}],
        },
    )
    assert response.status_code == 200
    assert response.json()["tests"][0]["rows"] == [["Cid"]]
