"""ТЗ Ф4 WP0 п.3г (Т-§6.5): which `antlr4-python3-runtime` the pinned SymPy needs, and whether
`parse_latex(..., backend="antlr", strict=True)` is available.

Run in the compute environment (SymPy is a compute dependency, `compute/uv.lock`):

    uv run --project compute python backend/scripts/checks/wp0_3g_antlr.py
    uv run --project compute --with "antlr4-python3-runtime==<version>" \
        python backend/scripts/checks/wp0_3g_antlr.py

Prints one JSON object; exit code 0 only when the runtime matches and a strict parse works.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import json
import re
import sys
from pathlib import Path


def main() -> int:
    import sympy
    from sympy.parsing.latex import parse_latex

    # SymPy checks the runtime itself: `version('antlr4-python3-runtime').startswith('4.11')`.
    source = Path(sympy.__file__).parent / "parsing" / "latex" / "_parse_latex_antlr.py"
    match = re.search(
        r"version\('antlr4-python3-runtime'\)\.startswith\('([\d.]+)'\)",
        source.read_text(encoding="utf-8"),
    )
    try:
        runtime: str | None = importlib.metadata.version("antlr4-python3-runtime")
    except importlib.metadata.PackageNotFoundError:
        runtime = None
    params = inspect.signature(parse_latex).parameters
    report: dict[str, object] = {
        "sympy": sympy.__version__,
        "antlr_required": match.group(1) if match else None,
        "antlr_runtime_installed": runtime,
        "parse_latex_has_backend": "backend" in params,
        "parse_latex_has_strict": "strict" in params,
    }
    try:
        expr = parse_latex(r"\frac{x^{2}}{2} + \sin(2x)", backend="antlr", strict=True)
        report["strict_parse"] = str(expr)
    except Exception as exc:
        report["strict_parse_error"] = f"{type(exc).__name__}: {exc}"
    if "strict_parse" in report:
        try:
            parse_latex(r"\frac{x}{", backend="antlr", strict=True)
            report["strict_rejects_incomplete"] = False
        except Exception:
            report["strict_rejects_incomplete"] = True
    print(json.dumps(report, ensure_ascii=False))
    ok = (
        report["antlr_required"] is not None
        and runtime is not None
        and runtime.startswith(str(report["antlr_required"]))
        and "strict_parse" in report
        and report.get("strict_rejects_incomplete") is True
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
