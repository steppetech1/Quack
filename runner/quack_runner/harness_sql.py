"""One test of an SQL task, inside the sandbox. Standalone script, stdlib only (see
`harness_python.py` for how it is started).

stdin, one JSON object:
    {"schema_sql": str, "data_sql": str, "query": str, "query_s": float, "max_rows": int,
     "result": {"fd": int} | {"path": str}}

The result channel gets one JSON object:
    {"status": "ok", "columns": [str], "rows": [[value]], "time_ms": int}
    {"status": "error" | "timeout" | "memory" | "row_limit" | "bad_input",
     "error": {"type": str, "message": str, "line": null}, "time_ms": int}

The database lives in memory and is built from the task's own scripts before the authorizer is
installed; the learner's query then may only read. Values: null, int, float, str as they are;
{"$float": "inf" | "-inf"}, {"$bytes": hex}.
"""

import json
import math
import os
import sqlite3
import sys
import time
from typing import NoReturn

MAX_MESSAGE = 500

ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
DENIED_FUNCTIONS = {"load_extension"}


def authorizer(action, arg1, arg2, db_name, trigger):
    if action not in ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def encode(value):
    if value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        if value != value:
            return None  # SQLite stores NaN as NULL
        return {"$float": "inf" if value > 0 else "-inf"}
    if isinstance(value, bytes):
        return {"$bytes": value.hex()}
    return str(value)


class Channel:
    def __init__(self, spec):
        if "fd" in spec:
            self._file = os.fdopen(int(spec["fd"]), "w", encoding="utf-8")
        else:
            self._file = open(spec["path"], "w", encoding="utf-8")  # noqa: SIM115
        self._start = time.perf_counter()

    def restart_clock(self):
        self._start = time.perf_counter()

    def emit(self, status, **fields) -> NoReturn:
        fields["status"] = status
        fields["time_ms"] = int((time.perf_counter() - self._start) * 1000)
        self._file.write(json.dumps(fields, ensure_ascii=False, allow_nan=False))
        self._file.flush()
        os._exit(0)


def error(exc):
    return {"type": type(exc).__name__, "message": str(exc)[:MAX_MESSAGE], "line": None}


def limit(conn, name, value):
    category = getattr(sqlite3, name, None)
    if category is not None and hasattr(conn, "setlimit"):
        conn.setlimit(category, value)


def main():
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    channel = Channel(request["result"])
    conn = sqlite3.connect(":memory:", isolation_level=None)
    if hasattr(conn, "enable_load_extension"):
        conn.enable_load_extension(False)
    try:
        conn.executescript(request["schema_sql"])
        conn.executescript(request["data_sql"])
    except (sqlite3.Error, MemoryError) as exc:
        channel.emit("bad_input", error=error(exc))

    limit(conn, "SQLITE_LIMIT_ATTACHED", 0)
    limit(conn, "SQLITE_LIMIT_LENGTH", 1_000_000)
    limit(conn, "SQLITE_LIMIT_SQL_LENGTH", 100_000)
    conn.set_authorizer(authorizer)
    deadline = time.monotonic() + float(request["query_s"])
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)

    max_rows = int(request["max_rows"])
    channel.restart_clock()
    try:
        cursor = conn.execute(request["query"])
        if cursor.description is None:
            channel.emit(
                "error",
                error={"type": "Error", "message": "запрос не вернул таблицу", "line": None},
            )
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchmany(max_rows + 1)
    except MemoryError as exc:
        channel.emit("memory", error=error(exc))
    except sqlite3.OperationalError as exc:
        if "interrupted" in str(exc):
            channel.emit("timeout", error=error(exc))
        channel.emit("error", error=error(exc))
    except (sqlite3.Error, sqlite3.Warning, ValueError) as exc:
        channel.emit("error", error=error(exc))
    if len(rows) > max_rows:
        channel.emit(
            "row_limit",
            error={"type": "Error", "message": f"больше {max_rows} строк", "line": None},
        )
    channel.emit("ok", columns=columns, rows=[[encode(v) for v in row] for row in rows])


if __name__ == "__main__":
    main()
