"""Prompt registry: prompts/{role}/v{N}.md with sha256 pinned in manifest.json (Т-§11.6).

A released prompt version is immutable: its hash is recorded in `manifest.json` as
"{role}@{N}": "<sha256>", and loading a file whose content differs is an error.
Templates use `{{ name }}` placeholders.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent
MANIFEST_NAME = "manifest.json"
_ROLE = re.compile(r"^[a-z][a-z0-9_]*$")
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


class PromptError(Exception):
    """Base error of the prompt registry."""


class PromptNotFoundError(PromptError):
    """The file or its manifest entry is missing."""


class PromptHashMismatchError(PromptError):
    """A released prompt file was edited in place."""


class PromptRenderError(PromptError):
    """A template variable is missing."""


@dataclass(frozen=True)
class PromptTemplate:
    role: str
    version: int
    text: str
    sha256: str

    @property
    def ref(self) -> str:
        return f"{self.role}@{self.version}"

    def render(self, variables: dict[str, Any]) -> str:
        missing = sorted({m for m in _PLACEHOLDER.findall(self.text) if m not in variables})
        if missing:
            raise PromptRenderError(f"{self.ref}: missing variables {missing}")

        def substitute(match: re.Match[str]) -> str:
            value = variables[match.group(1)]
            return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

        return _PLACEHOLDER.sub(substitute, self.text)


class PromptRegistry:
    def __init__(self, base_dir: Path = PROMPTS_DIR) -> None:
        self.base_dir = base_dir

    def manifest(self) -> dict[str, str]:
        path = self.base_dir / MANIFEST_NAME
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return {str(k): str(v) for k, v in data.items()}

    def load(self, role: str, version: int) -> PromptTemplate:
        if not _ROLE.fullmatch(role) or version < 1:
            raise PromptNotFoundError(f"invalid prompt reference {role}@{version}")
        ref = f"{role}@{version}"
        path = self.base_dir / role / f"v{version}.md"
        expected = self.manifest().get(ref)
        if expected is None or not path.is_file():
            raise PromptNotFoundError(f"prompt {ref} is not registered in {MANIFEST_NAME}")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != expected:
            raise PromptHashMismatchError(f"prompt {ref} changed: sha256 {digest} != {expected}")
        return PromptTemplate(role=role, version=version, text=raw.decode("utf-8"), sha256=digest)
