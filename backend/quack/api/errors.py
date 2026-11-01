"""Unified ErrorResponse (spec §8.5)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from neo4j.exceptions import ConnectionAcquisitionTimeoutError, ServiceUnavailable, SessionExpired
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from quack.events.errors import IdempotencyConflictError
from quack.llm.errors import LLMBudgetExceededError, LLMSchemaError, LLMUnavailableError
from quack.logging import get_logger

log = get_logger(__name__)

type ErrorCode = Literal[
    "IDEMPOTENCY_CONFLICT",
    "TURN_LOCKED",
    "LLM_DEGRADED",
    "NOT_FOUND",
    "VALIDATION_ERROR",
    "UNAUTHORIZED",
    "RATE_LIMITED",
    # Phase 3 §4.5: a dependency needed for the answer (Neo4j, compute without fallback) is down.
    "SERVICE_UNAVAILABLE",
]

# §4.5, WP6.1 (seam S7): driver errors meaning Neo4j is unreachable (server down, expired
# session, no connection in time). Query errors are bugs and stay 500.
NEO4J_OUTAGE_ERRORS: tuple[type[Exception], ...] = (
    ServiceUnavailable,
    SessionExpired,
    ConnectionAcquisitionTimeoutError,
)

_STATUS_CODES: dict[int, ErrorCode] = {
    401: "UNAUTHORIZED",
    403: "UNAUTHORIZED",
    404: "NOT_FOUND",
    405: "NOT_FOUND",
    409: "IDEMPOTENCY_CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    503: "SERVICE_UNAVAILABLE",
}


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    request_id: str
    details: dict[str, Any] = {}


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def request_id_of(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def error_response(
    request: Request,
    code: ErrorCode,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorBody(
            code=code, message=message, request_id=request_id_of(request), details=details or {}
        )
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=status_code)


def install_error_handlers(app: FastAPI) -> None:
    async def api_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, ApiError)
        return error_response(request, exc.code, exc.message, exc.status_code, exc.details)

    async def validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        errors = [
            {"loc": list(e.get("loc", ())), "msg": str(e.get("msg", ""))} for e in exc.errors()
        ]
        return error_response(
            request, "VALIDATION_ERROR", "invalid request", 422, {"errors": errors}
        )

    async def http_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        code = _STATUS_CODES.get(exc.status_code, "VALIDATION_ERROR")
        return error_response(request, code, str(exc.detail), exc.status_code)

    async def idempotency_conflict(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, "IDEMPOTENCY_CONFLICT", str(exc), 409)

    async def llm_degraded(request: Request, exc: Exception) -> JSONResponse:
        return error_response(request, "LLM_DEGRADED", str(exc), 503)

    async def neo4j_unavailable(request: Request, exc: Exception) -> JSONResponse:
        log.warning("neo4j_unavailable", path=request.url.path, error=repr(exc))
        return error_response(
            request, "SERVICE_UNAVAILABLE", "graph store temporarily unavailable", 503
        )

    app.add_exception_handler(ApiError, api_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(IdempotencyConflictError, idempotency_conflict)
    for neo4j_error in NEO4J_OUTAGE_ERRORS:
        app.add_exception_handler(neo4j_error, neo4j_unavailable)
    for exc_type in (LLMUnavailableError, LLMBudgetExceededError, LLMSchemaError):
        app.add_exception_handler(exc_type, llm_degraded)
