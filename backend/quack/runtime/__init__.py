"""Process resources and handler registration of `workers` / `projector` (ТЗ Фазы 3 §4.2, WP2).

Stage I: signatures only. Bodies arrive with WP2.1 and WP2.2.
"""

from __future__ import annotations

from quack.config import Settings
from quack.jobs.runner import JobContext, JobHandler


async def create_job_context(settings: Settings) -> JobContext:
    """Build the resources of §4.2 once per process, in creation order (WP2.1)."""
    raise NotImplementedError("WP2.1: stage III")


async def close_job_context(context: JobContext) -> None:
    """Close in reverse creation order (§4.2)."""
    raise NotImplementedError("WP2.1: stage III")


def register_all_handlers() -> dict[str, JobHandler]:
    """Import every product module with `@register_job_handler`; return the registry (WP2.2)."""
    raise NotImplementedError("WP2.2: stage III")


def check_handlers_complete(handlers: dict[str, JobHandler]) -> None:
    """Raise `JobHandlersMismatchError` unless `handlers` keys equal `JOB_KINDS` (WP2.2)."""
    raise NotImplementedError("WP2.2: stage III")


class JobHandlersMismatchError(RuntimeError):
    """`job_handlers_mismatch`: registered handlers differ from `JOB_KINDS` (T-Q7)."""

    def __init__(self, missing: list[str], extra: list[str]) -> None:
        super().__init__(f"job_handlers_mismatch: missing={missing} extra={extra}")
        self.missing = missing
        self.extra = extra
