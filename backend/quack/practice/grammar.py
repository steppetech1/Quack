"""Token grammar of math input (Т-§6.5, Т-§22.7) on the API side (ТЗ Ф4 WP12.3, D-B-115).

The whitelist is a grammar of tokens, not a list of names: numbers (a dot only inside a
number), operators, round and square brackets, comma, `=`, identifiers of letters and
digits with one optional subscript (`v_0`, `F_net`) — no leading or double `_`. ASCII only:
Unicode look-alikes are refused. Anything else is a parse refusal. SymPy itself runs only in
compute (`/v1/math/check`); this check lets `validate_markup` refuse a spec that compute
would refuse, without a network call.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(
    r"\s+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)?"
    r"|\*\*|[*/+\-^()\[\],=]",
    re.ASCII,
)
_BRACKETS = {")": "(", "]": "["}


def grammar_ok(text: str) -> bool:
    """True if `text` is a sequence of allowed tokens with balanced brackets."""
    if not text.strip():
        return False
    pos = 0
    stack: list[str] = []
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None:
            return False
        token = match.group()
        if token in "([":
            stack.append(token)
        elif token in _BRACKETS and (not stack or stack.pop() != _BRACKETS[token]):
            return False
        pos = match.end()
    return not stack
