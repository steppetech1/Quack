"""Settings of the runner (documentation/идея-редактор-кода-и-запуск.md, «Лимиты по умолчанию»).

Read from the environment once per process (the upper-case field name with the `RUNNER_`
prefix). The runner has no secrets. Numbers are the starting defaults of the document; they are
tuned by measurements, so they live here and not in code.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from dataclasses import dataclass


def _default_sandbox() -> bool:
    # The real sandbox needs Linux and root (to drop to the slot uids); a checkout on a laptop
    # runs the same code without the uid switch and the resource limits.
    return sys.platform == "linux" and os.geteuid() == 0


@dataclass(frozen=True)
class RunnerSettings:
    # Switch to slot uids, set resource limits, kill every process of the slot after a run.
    sandbox: bool = dataclasses.field(default_factory=_default_sandbox)
    # The interpreter of learner code; `-S` keeps the runner's own packages out of reach.
    python: str = sys.executable
    # Slots: one run at a time each, own uid and own directory `<work_dir>/slot-<n>`. The uids
    # are far from 10001 (the compute user): without a user namespace a uid is shared with
    # every container of the host, and RLIMIT_NPROC counts all its processes.
    slots: int = 4
    slot_uid_base: int = 20000
    work_dir: str = "/work"
    # How long a request waits for a free slot before 429.
    slot_wait_s: float = 2.0

    # Python, one test.
    py_cpu_s: int = 2
    py_wall_s: float = 5.0
    py_memory_mb: int = 256
    py_nproc: int = 16
    # Python, one run (all tests of a request).
    py_total_wall_s: float = 20.0

    # SQL, one test: the query is interrupted by the progress handler past `sql_query_s`; the
    # process itself is killed past `sql_wall_s`.
    sql_query_s: float = 2.0
    sql_wall_s: float = 4.0
    sql_memory_mb: int = 128
    # SQLite does not fork; a little room so a process of the previous test that is still being
    # reaped does not make the next exec fail with EAGAIN.
    sql_nproc: int = 4
    sql_total_wall_s: float = 10.0
    sql_max_rows: int = 10_000

    # Both languages.
    output_bytes: int = 64 * 1024
    result_bytes: int = 4 * 1024 * 1024
    file_bytes: int = 16 * 1024 * 1024
    open_files: int = 64

    @classmethod
    def from_env(cls) -> RunnerSettings:
        values: dict[str, object] = {}
        for field in dataclasses.fields(cls):
            raw = os.environ.get(f"RUNNER_{field.name.upper()}")
            if raw is None:
                continue
            current = getattr(cls(), field.name)
            if isinstance(current, bool):
                values[field.name] = raw.strip().lower() in {"1", "true", "yes"}
            elif isinstance(current, int):
                values[field.name] = int(raw)
            elif isinstance(current, float):
                values[field.name] = float(raw)
            else:
                values[field.name] = raw
        return cls(**values)  # type: ignore[arg-type]
