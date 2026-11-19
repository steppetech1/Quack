"""Closing the canvas socket: a failed helper task must not skip the journal flush (WP21)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

from quack.canvas.socket import stop_task


async def test_stop_task_swallows_an_error_the_task_already_ended_with() -> None:
    async def broken() -> None:
        raise RuntimeError("send on a closed socket")

    task = asyncio.create_task(broken())
    await asyncio.sleep(0)  # the task runs and ends with the error
    assert task.done()

    await stop_task(task, canvas_id=uuid4())  # does not raise


async def test_stop_task_cancels_a_running_task() -> None:
    started = asyncio.Event()

    async def forever() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(forever())
    await started.wait()

    await stop_task(task, canvas_id=uuid4())

    assert task.cancelled()


async def test_stop_task_accepts_no_task() -> None:
    await stop_task(None, canvas_id=uuid4())
