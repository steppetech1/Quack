"""Smoke tests for the application factory and HTTP contracts."""

import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import health as health_module
from app.errors import NotFound
from app.events import dispatch as dispatcher
from app.main import create_app


@pytest.fixture
def app():
    return create_app()


def test_create_app(app):
    assert isinstance(app, FastAPI)


def test_openapi_title(app):
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Quack API"


@pytest.mark.phase2
def test_openapi_contains_phase2_routes(app):
    paths = app.openapi()["paths"]
    for path in (
        "/tasks",
        "/sets",
        "/knowledge",
        "/matching",
        "/matching/compare",
        "/overview",
        "/overview/milestones/{key}",
        "/diagnostic",
        "/diagnostic/active",
        "/diagnostic/{run_id}/answer",
        "/diagnostic/{run_id}/finish",
        "/mocks",
        "/mocks/{run_id}",
        "/mocks/{run_id}/answer",
        "/mocks/{run_id}/finish",
    ):
        assert path in paths


@pytest.mark.phase2
def test_multiple_app_factories_do_not_register_handlers_again():
    before = {
        event: tuple(handlers) for event, handlers in dispatcher._handlers.items()
    }
    create_app()
    create_app()
    assert {
        event: tuple(handlers) for event, handlers in dispatcher._handlers.items()
    } == before


def test_unknown_route(app):
    with TestClient(app) as client:
        response = client.get("/unknown")
    assert response.status_code == 404
    assert re.fullmatch(r"[0-9a-f]{16}", response.headers["X-Request-Id"])


def test_app_error_contract(app):
    @app.get("/test-error")
    async def fail():
        raise NotFound("program not found")

    with TestClient(app) as client:
        response = client.get("/test-error", headers={"X-Request-Id": "error-id"})
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": "program not found"}
    }
    assert response.headers["X-Request-Id"] == "error-id"


def test_request_validation_returns_400(app):
    @app.get("/test-validation")
    async def validate(value: int):
        return {"value": value}

    with TestClient(app) as client:
        response = client.get("/test-validation", params={"value": "not-an-int"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_failed"
    assert "integer" in response.json()["error"]["message"]
    assert "X-Request-Id" in response.headers


def test_health_degraded_and_incoming_request_id(app, monkeypatch):
    # Force one store check down so the result doesn't depend on whether
    # local Postgres/Neo4j/Redis happen to be running.
    async def unhealthy(_request):
        raise OSError("neo4j is down")

    monkeypatch.setattr(health_module, "_neo4j", unhealthy)
    with TestClient(app) as client:
        response = client.get("/health", headers={"X-Request-Id": "existing-id"})
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    # Фаза 4 (§6.2): проверка поиска читает последнюю ошибку из Redis и
    # `skipped` остаётся только там, где Redis недоступен.
    assert response.json()["checks"]["search"] in {"ok", "skipped"}
    assert response.json()["version"] == "dev"
    assert response.headers["X-Request-Id"] == "existing-id"


@pytest.mark.parametrize("request_id", [None, "failure-id"])
def test_unhandled_exception_hides_details_and_returns_request_id(app, request_id):
    @app.get("/test-unhandled")
    async def fail():
        raise RuntimeError("private exception details")

    headers = {"X-Request-Id": request_id} if request_id else {}
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-unhandled", headers=headers)
    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal", "message": "Internal server error"},
        "request_id": response.headers["X-Request-Id"],
    }
    if request_id:
        assert response.headers["X-Request-Id"] == request_id
    else:
        assert re.fullmatch(r"[0-9a-f]{16}", response.headers["X-Request-Id"])


def test_unhandled_exception_carries_cors_headers(app):
    """A 500 must be readable by the browser, not look like a dropped connection."""

    @app.get("/test-unhandled-cors")
    async def fail():
        raise RuntimeError("private exception details")

    origin = "http://localhost:3000"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-unhandled-cors", headers={"Origin": origin})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert response.headers["X-Request-Id"] == response.json()["request_id"]
