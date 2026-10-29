"""Session lifecycle: start, active, get, heartbeat, finish and the inactivity sweep (ТЗ §2.2)."""

from __future__ import annotations

from uuid import UUID

from quack.api.app import AppResources
from quack.config import Settings
from quack.jobs.queue import ClaimedJob
from quack.jobs.runner import JobContext, register_job_handler
from quack.session.schemas import (
    ActiveSession,
    FinishResponse,
    FinishSessionRequest,
    HeartbeatResponse,
    SessionDetail,
    SessionStarted,
    StartSessionRequest,
)


async def start_session(
    resources: AppResources, settings: Settings, learner_id: UUID, body: StartSessionRequest
) -> SessionStarted:
    raise NotImplementedError


async def get_active_session(resources: AppResources, learner_id: UUID) -> ActiveSession | None:
    raise NotImplementedError


async def get_session(resources: AppResources, learner_id: UUID, session_id: UUID) -> SessionDetail:
    raise NotImplementedError


async def heartbeat(
    resources: AppResources, settings: Settings, learner_id: UUID, session_id: UUID
) -> HeartbeatResponse:
    raise NotImplementedError


async def finish_session(
    resources: AppResources, learner_id: UUID, session_id: UUID, body: FinishSessionRequest
) -> FinishResponse:
    raise NotImplementedError


@register_job_handler("session.sweep_inactive")
async def sweep_inactive_sessions(job: ClaimedJob, ctx: JobContext) -> None:
    raise NotImplementedError
