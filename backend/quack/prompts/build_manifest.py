"""Rebuild `manifest.json` from the prompt files (ТЗ Фазы 3 §7.3, §7.6 п.3).

Hashes are never written by hand: every `prompts/<role>/v<N>.md` gets the key `"<role>@<N>"`
with the sha256 of its bytes. Run: `uv run python -m quack.prompts.build_manifest`.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from quack.prompts.registry import MANIFEST_NAME, PROMPTS_DIR

_VERSION_FILE = re.compile(r"^v([1-9][0-9]*)\.md$")


def compute_manifest(base_dir: Path = PROMPTS_DIR) -> dict[str, str]:
    """`{"role@N": sha256}` for every prompt file, sorted by role and version."""
    entries: list[tuple[str, int, str]] = []
    for role_dir in sorted(p for p in base_dir.iterdir() if p.is_dir()):
        for path in role_dir.iterdir():
            match = _VERSION_FILE.fullmatch(path.name)
            if match is None:
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append((role_dir.name, int(match.group(1)), digest))
    return {f"{role}@{version}": digest for role, version, digest in sorted(entries)}


def write_manifest(base_dir: Path = PROMPTS_DIR) -> dict[str, str]:
    manifest = compute_manifest(base_dir)
    text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    (base_dir / MANIFEST_NAME).write_text(text, encoding="utf-8")
    return manifest


if __name__ == "__main__":
    for ref in write_manifest():
        print(ref)
