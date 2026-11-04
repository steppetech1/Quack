"""T-DB3 (ТЗ Ф4 WP1.3, DoD п.6): no SQLAlchemy, asyncpg or Alembic is left in the code or in the
dependencies; PyJWT and pwdlib went with the move to the cookie session (DoD п.6)."""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
CODE_ROOTS = ("quack", "tests", "loadtest")
GONE = ("sqlalchemy", "asyncpg", "alembic")
GONE_PACKAGES = ("sqlalchemy", "asyncpg", "alembic", "pyjwt", "pwdlib")


def _imports(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def test_no_sqlalchemy_left() -> None:
    offenders = [
        f"{path.relative_to(BACKEND)}: {name}"
        for root in CODE_ROOTS
        if (BACKEND / root).is_dir()
        for path in sorted((BACKEND / root).rglob("*.py"))
        for name in sorted(_imports(path) & set(GONE))
    ]
    assert offenders == []

    project = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    declared = list(project.get("dependencies", []))
    for group in project.get("optional-dependencies", {}).values():
        declared.extend(group)
    for group in (
        tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
        .get("dependency-groups", {})
        .values()
    ):
        declared.extend(item for item in group if isinstance(item, str))
    names = {re.split(r"[\s\[<>=!~;@(]", dep.strip().lower(), maxsplit=1)[0] for dep in declared}
    left = sorted(names & set(GONE_PACKAGES))
    assert left == []
