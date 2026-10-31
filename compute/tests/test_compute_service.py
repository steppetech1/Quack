"""compute service tests (ТЗ §5 п.1): six modes, parser safety, timeout of the child."""

from __future__ import annotations

import multiprocessing
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from quack_compute.app import app

# Degree-31 polynomial with coprime coefficients: `solveset` takes ~10 s on it.
HEAVY_EQUATION = "x**31 + 7*x**17 - 13*x**5 + 3*x - 101 = 0"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://compute") as client:
        yield client


async def check(
    client: httpx.AsyncClient, mode: str, student: str, reference: str, **fields: Any
) -> dict[str, Any]:
    response = await client.post(
        "/v1/check/math",
        json={"mode": mode, "student_expr": student, "reference_expr": reference, **fields},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_expression_equiv_and_numeric_tol(client: httpx.AsyncClient) -> None:
    health = await client.get("/health/live")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}

    result = await check(client, "expression_equiv", "3x^2", "3*x**2")
    assert result["status"] == "completed"
    assert result["is_equivalent"] is True

    result = await check(client, "expression_equiv", "sin(x)**2 + cos(x)**2", "1")
    assert result["is_equivalent"] is True

    result = await check(client, "expression_equiv", "x + 1", "x + 2")
    assert result["status"] == "completed"
    assert result["is_equivalent"] is False

    result = await check(client, "expression_equiv", "sqrt(x**2)", "x")
    assert result["is_equivalent"] is False

    result = await check(client, "numeric_tol", "0.3333333", "1/3", tolerance=1e-6)
    assert result["is_equivalent"] is True
    result = await check(client, "numeric_tol", "0.3333333", "1/3", tolerance=1e-9)
    assert result["is_equivalent"] is False


async def test_equation_roots_and_matrix(client: httpx.AsyncClient) -> None:
    reference = "x**2 - 4 = 0"
    result = await check(client, "equation_roots", "2, -2", reference)
    assert result["is_equivalent"] is True
    result = await check(client, "equation_roots", "x = 2", reference)
    assert result["is_equivalent"] is False
    result = await check(client, "equation_roots", "(x - 2)*(x + 2)", reference)
    assert result["is_equivalent"] is True

    matrix = "Matrix([[1, 2], [3, 4]])"
    result = await check(client, "matrix_eq", matrix, "[[1, 2], [3, 4]]")
    assert result["is_equivalent"] is True
    result = await check(client, "matrix_eq", matrix, "[[1, 2]]")
    assert result["is_equivalent"] is False


async def test_derivative_and_integral_with_constant(client: httpx.AsyncClient) -> None:
    result = await check(client, "derivative", "3x^2", "x**3")
    assert result["is_equivalent"] is True
    result = await check(client, "derivative", "6*x", "x**3")
    assert result["is_equivalent"] is False

    result = await check(client, "integral", "x**3/3 + 5", "x**3/3")
    assert result["is_equivalent"] is True
    result = await check(client, "integral", "x**3", "x**3/3")
    assert result["is_equivalent"] is False


async def test_parser_rejects_code(client: httpx.AsyncClient, tmp_path: Path) -> None:
    marker = tmp_path / "marker"
    attacks = [
        f"__import__('os').system('touch {marker}')",
        "x.__class__",
        "().__class__.__bases__",
        "open('f')",
        "lambda: 1",
        "getattr(x, 'y')",
        "x; y",
        "x @@ y",
        "1 +",
    ]
    for attack in attacks:
        result = await check(client, "expression_equiv", attack, "x")
        assert result["status"] == "parse_error", attack
        assert result["is_equivalent"] is None, attack
        assert result["error_message"], attack
    assert not marker.exists()

    response = await client.post(
        "/v1/check/math",
        json={"mode": "expression_equiv", "student_expr": "x" * 1001, "reference_expr": "x"},
    )
    assert response.status_code == 422


async def test_timeout_kills_child(client: httpx.AsyncClient) -> None:
    started = time.monotonic()
    result = await check(client, "equation_roots", "1", HEAVY_EQUATION, timeout_ms=50)
    assert time.monotonic() - started < 3.0
    assert result["status"] == "timeout"
    assert result["is_equivalent"] is None
    assert multiprocessing.active_children() == []
