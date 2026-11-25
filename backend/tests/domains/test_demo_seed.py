"""DEMO-ONLY seed of a domain's map from the learner's text (`quack.domains.demo_seed`)."""

from __future__ import annotations

from quack.domains.demo_seed import outline


def test_outline_takes_the_list_after_the_colon() -> None:
    names = outline("I want to learn calculus: limits, derivatives and integrals", "Calculus")
    assert names == ["Limits", "Derivatives", "Integrals"]


def test_outline_falls_back_to_the_template() -> None:
    names = outline("I want to learn Rust", "Rust")
    assert len(names) == 5 and all("Rust" in n for n in names)
