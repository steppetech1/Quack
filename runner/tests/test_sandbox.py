"""The real sandbox (Linux, root with SETUID/SETGID/KILL, slot tmpfs): run in the runner image
in CI (`docker run ... pytest -m sandbox`). Skipped where the sandbox is off."""

from __future__ import annotations

import asyncio

import pytest

from quack_runner.contracts import PythonRunRequest
from quack_runner.sandbox import SlotPool, SlotsBusy
from quack_runner.service import run_python

pytestmark = pytest.mark.sandbox


@pytest.fixture(autouse=True)
def _needs_sandbox(settings) -> None:
    if not settings.sandbox:
        pytest.skip("the sandbox is off here (not Linux root)")


async def test_runs_as_slot_uid(py, settings) -> None:
    (result,) = await py("import os\ndef f():\n    return os.getuid()\n", [])
    assert result.value is not None
    assert settings.slot_uid_base < result.value <= settings.slot_uid_base + settings.slots


async def test_fork_bomb_is_contained(py) -> None:
    code = (
        "import os\n"
        "def f():\n"
        "    for _ in range(10_000):\n"
        "        try:\n"
        "            os.fork()\n"
        "        except OSError:\n"
        "            pass\n"
        "    return 1\n"
    )
    await py(code, [])
    (after,) = await py("def f():\n    return 2\n", [])
    assert after.status == "ok" and after.value == 2


async def test_memory_limit(py) -> None:
    (result,) = await py("def f():\n    return len(bytearray(1024 * 1024 * 1024))\n", [])
    assert result.status == "memory"


async def test_left_process_is_killed(py) -> None:
    start = (
        "import os, time\n"
        "def f():\n"
        "    if os.fork() == 0:\n"
        "        os.setsid()\n"
        "        time.sleep(60)\n"
        "        os._exit(0)\n"
        "    return os.getuid()\n"
    )
    (first,) = await py(start, [])
    check = (
        "import os\n"
        "def f(uid):\n"
        "    alive = []\n"
        "    for pid in os.listdir('/proc'):\n"
        "        if pid.isdigit():\n"
        "            try:\n"
        "                if os.stat('/proc/' + pid).st_uid == uid and int(pid) != os.getpid():\n"
        "                    alive.append(pid)\n"
        "            except OSError:\n"
        "                pass\n"
        "    return alive\n"
    )
    (second,) = await py(check, [first.value])
    assert second.value == []


async def test_other_slot_directory_is_closed(py, settings) -> None:
    code = (
        "import os\n"
        "def f(root, base, count):\n"
        "    mine = os.getuid() - base\n"
        "    readable = []\n"
        "    for n in range(1, count + 1):\n"
        "        if n != mine:\n"
        "            try:\n"
        "                os.listdir(f'{root}/slot-{n}')\n"
        "                readable.append(n)\n"
        "            except OSError:\n"
        "                pass\n"
        "    return readable\n"
    )
    (result,) = await py(code, [settings.work_dir, settings.slot_uid_base, settings.slots])
    assert result.value == []


async def test_no_network(py) -> None:
    code = (
        "import socket\n"
        "def f():\n"
        "    try:\n"
        "        socket.create_connection(('1.1.1.1', 80), timeout=1)\n"
        "        return 'connected'\n"
        "    except OSError:\n"
        "        return 'blocked'\n"
    )
    (result,) = await py(code, [])
    assert result.value == "blocked"


async def test_busy_slots_answer_429(settings) -> None:
    import dataclasses

    tight = dataclasses.replace(settings, slots=1, slot_wait_s=0.2)
    pool = SlotPool(tight)
    pool.start()
    slow = PythonRunRequest.model_validate(
        {
            "code": "import time\ndef f():\n    time.sleep(1)\n",
            "function": "f",
            "tests": [{"args": []}],
        }
    )
    first = asyncio.create_task(run_python(slow, pool, tight))
    await asyncio.sleep(0.05)
    with pytest.raises(SlotsBusy):
        await run_python(slow, pool, tight)
    await first
