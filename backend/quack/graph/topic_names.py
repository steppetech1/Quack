"""Canonical topic naming and duplicate-prefix stripping (Т-§4.4)."""

from __future__ import annotations


def canonical_topic_name(name: str, domain_name: str | None = None) -> str:
    """Collapse whitespace and strip a redundant leading `<DomainName>: ` prefix if non-empty
    remains."""
    cleaned = " ".join(name.split())
    if not cleaned or not domain_name:
        return cleaned
    dom = " ".join(domain_name.split())
    if not dom:
        return cleaned
    folded_clean = cleaned.casefold()
    folded_dom = dom.casefold()
    for sep in (": ", " - ", " — ", " – ", ":"):
        prefix = folded_dom + sep
        if folded_clean.startswith(prefix):
            rest = cleaned[len(prefix) :].strip()
            if rest:
                return rest
    return cleaned


def normalized_topic_key(name: str, domain_name: str | None = None) -> str:
    """Case-folded canonical key of a topic name."""
    return " ".join(canonical_topic_name(name, domain_name).casefold().split())
