"""One test of a Python task, inside the sandbox. Standalone script, stdlib only: it runs as
`python -S -s -B -P harness_python.py` under the slot uid, never imported by the runner.

stdin, one JSON object:
    {"code": str, "function": str, "args": [value, ...], "return_args": bool,
     "seed": int, "result": {"fd": int} | {"path": str}}

The result channel gets one JSON object:
    {"status": "ok", "value": value, "args_after": [value, ...] | null, "time_ms": int}
    {"status": "error" | "memory" | "no_function" | "unsupported_value" | "bad_input",
     "error": {"type": str, "message": str, "line": int | null}, "time_ms": int}

Values are tagged JSON: null, bool, int, float, str and list as they are; {"$float": "nan" |
"inf" | "-inf"}, {"$tuple": [...]}, {"$set": [...]}, {"$dict": [[key, value], ...]}.

Learner code runs in this process and can tamper with anything here, the result channel
included. That is harmless: expected answers never reach the sandbox and the backend compares,
so a forged result is only a value the learner chose, the same as returning it.
"""

import inspect
import json
import math
import os
import random
import sys
import time
import traceback
from typing import NoReturn

MAX_DEPTH = 64
MAX_ITEMS = 100_000
MAX_MESSAGE = 500
SOURCE_NAME = "solution.py"


class Unsupported(Exception):
    pass


def decode(value, depth=0):
    if depth > MAX_DEPTH:
        raise ValueError("value nested too deep")
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [decode(item, depth + 1) for item in value]
    if isinstance(value, dict) and len(value) == 1:
        ((tag, body),) = value.items()
        if tag == "$float" and body in ("nan", "inf", "-inf"):
            return float(body)
        if tag == "$tuple" and isinstance(body, list):
            return tuple(decode(item, depth + 1) for item in body)
        if tag == "$set" and isinstance(body, list):
            return {decode(item, depth + 1) for item in body}
        if tag == "$dict" and isinstance(body, list):
            return {decode(k, depth + 1): decode(v, depth + 1) for k, v in body}
    raise ValueError("unknown value shape")


def encode(value, budget, depth=0):
    """Plain types only, read through the base classes: a subclass with its own `__eq__`,
    `__iter__` or `__str__` cannot change what is reported."""
    if depth > MAX_DEPTH:
        raise Unsupported("значение вложено глубже 64 уровней")
    budget[0] += 1
    if budget[0] > MAX_ITEMS:
        raise Unsupported("в значении больше 100000 элементов")
    kind = type(value)
    if value is None:
        return None
    if kind is bool:
        return value
    if issubclass(kind, int) and not issubclass(kind, bool):
        return int.__int__(value)
    if issubclass(kind, float):
        number = float.__float__(value)
        if math.isfinite(number):
            return number
        if number != number:
            return {"$float": "nan"}
        return {"$float": "inf" if number > 0 else "-inf"}
    if issubclass(kind, str):
        return str.__str__(value)
    if issubclass(kind, list):
        return [encode(item, budget, depth + 1) for item in list.__iter__(value)]
    if issubclass(kind, tuple):
        return {"$tuple": [encode(item, budget, depth + 1) for item in tuple.__iter__(value)]}
    if issubclass(kind, (set, frozenset)):
        iterate = set.__iter__ if issubclass(kind, set) else frozenset.__iter__
        items = [encode(item, budget, depth + 1) for item in iterate(value)]
        items.sort(key=lambda item: json.dumps(item, sort_keys=True))
        return {"$set": items}
    if issubclass(kind, dict):
        return {
            "$dict": [
                [encode(k, budget, depth + 1), encode(v, budget, depth + 1)]
                for k, v in dict.items(value)
            ]
        }
    raise Unsupported(f"тип {kind.__name__} не поддерживается")


class Channel:
    def __init__(self, spec):
        if "fd" in spec:
            self._file = os.fdopen(int(spec["fd"]), "w", encoding="utf-8")
        else:
            self._file = open(spec["path"], "w", encoding="utf-8")  # noqa: SIM115
        self._start = time.perf_counter()

    def emit(self, status, **fields) -> NoReturn:
        fields["status"] = status
        fields["time_ms"] = int((time.perf_counter() - self._start) * 1000)
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        self._file.write(json.dumps(fields, ensure_ascii=False, allow_nan=False))
        self._file.flush()
        os._exit(0)

    def restart_clock(self):
        self._start = time.perf_counter()


def describe(exc):
    try:
        message = str(exc)
    except BaseException:
        message = ""
    line = None
    for frame in traceback.extract_tb(exc.__traceback__):
        if frame.filename == SOURCE_NAME:
            line = frame.lineno
    if isinstance(exc, SyntaxError) and exc.filename == SOURCE_NAME:
        line = exc.lineno
    return {"type": type(exc).__name__, "message": message[:MAX_MESSAGE], "line": line}


def main():
    request = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    channel = Channel(request["result"])
    random.seed(request.get("seed", 0))
    try:
        args = [decode(arg) for arg in request["args"]]
    except Exception as exc:
        channel.emit("bad_input", error=describe(exc))

    namespace = {"__name__": "solution", "__builtins__": __builtins__}
    try:
        exec(compile(request["code"], SOURCE_NAME, "exec"), namespace)
    except MemoryError as exc:
        channel.emit("memory", error=describe(exc))
    except BaseException as exc:
        channel.emit("error", error=describe(exc))

    name = request["function"]
    function = namespace.get(name)
    if not callable(function):
        channel.emit(
            "no_function",
            error={"type": "NameError", "message": f"не найдена функция {name}", "line": None},
        )
    try:
        inspect.signature(function).bind(*args)
    except TypeError as exc:
        channel.emit(
            "no_function",
            error={"type": "TypeError", "message": f"{name}: {exc}"[:MAX_MESSAGE], "line": None},
        )
    except ValueError:
        pass  # no signature to inspect (a builtin): the call itself will tell

    channel.restart_clock()
    try:
        value = function(*args)
    except MemoryError as exc:
        channel.emit("memory", error=describe(exc))
    except BaseException as exc:
        channel.emit("error", error=describe(exc))

    try:
        encoded = encode(value, [0])
        after = [encode(arg, [0]) for arg in args] if request.get("return_args") else None
    except Unsupported as exc:
        channel.emit(
            "unsupported_value",
            error={"type": "TypeError", "message": str(exc), "line": None},
        )
    except MemoryError as exc:
        channel.emit("memory", error=describe(exc))
    channel.emit("ok", value=encoded, args_after=after)


if __name__ == "__main__":
    main()
