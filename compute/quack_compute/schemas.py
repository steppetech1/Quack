"""Request and response of `POST /v1/check/math`, field for field as in
`backend/quack/compute_client/client.py` (spec §8.4)."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

type MathMode = Literal[
    "expression_equiv", "equation_roots", "derivative", "integral", "matrix_eq", "numeric_tol"
]


class MathCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: MathMode
    student_expr: Annotated[str, Field(max_length=1000)]
    reference_expr: Annotated[str, Field(max_length=1000)]
    variables: list[str] = ["x"]
    tolerance: float = 1e-6
    timeout_ms: int = 1500


class MathCheckResponse(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    status: Literal["completed", "parse_error", "timeout", "unsupported"]
    is_equivalent: bool | None
    normalized_student: str | None
    diff_hint: str | None
    error_message: str | None
