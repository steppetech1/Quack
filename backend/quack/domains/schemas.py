"""Pydantic schemas of the domains stream (ТЗ Ф4 §6.4; Т-§4, Т-§8.6, Т-§22.2).

HTTP models of the routes of stream A: domains, preferences, the graph of a domain. The state of
a topic is `TopicDisplay` (codes and parameters, not text, `Т-§22.2`).

The structured outputs of the roles `structurer`, `diagnostic` and `curator` at the end of the
file are not routes of this stream: the roles stay in the LLM stack of stream B (prompts, role
table, contract tests) until B removes them with the Phase 3 prompts (D-A-208).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from quack.events.payloads import Unit
from quack.graph.read.models import TopicDisplay, TopicRequires

__all__ = [
    "CreateDomainRequest",
    "CreateDomainResponse",
    "CuratorMerge",
    "CuratorOutput",
    "CuratorPromotion",
    "DiagnosticOutput",
    "DomainGraphResponse",
    "DomainListResponse",
    "DomainSummary",
    "SetPreferenceRequest",
    "SetPreferenceResponse",
    "StructurerMisconception",
    "StructurerOutput",
    "StructurerPrereq",
    "StructurerSkill",
    "StructurerSkillDep",
    "StructurerTopic",
    "TopicNodeView",
]


# --- 1. Learner preferences (Т-§9.5) ---

# The key becomes the property `pref<Key>` of the learner's `Profile` (WP5.3): a short snake_case
# name, so a key can never be a path into anything else (D-A-207).
PreferenceKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,31}$")]


class SetPreferenceRequest(BaseModel):
    """`preference.set {key, value}` (ТЗ Ф4 §6.4 «Ученик»)."""

    model_config = ConfigDict(extra="forbid")

    key: PreferenceKey
    value: Annotated[str, Field(max_length=500)] | int | float | bool
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]


class SetPreferenceResponse(BaseModel):
    key: str
    value: str | int | float | bool
    seq: int


# --- 2. Domains (Т-§4.8) ---


class CreateDomainRequest(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=200)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200)]


class CreateDomainResponse(BaseModel):
    domain_id: UUID
    name: str
    seq: int


class DomainSummary(BaseModel):
    """A domain of the learner, from its `domain.created` (ТЗ Ф4 §6.1)."""

    domain_id: UUID
    name: str
    created_seq: int
    created_at: datetime


class DomainListResponse(BaseModel):
    domains: list[DomainSummary]


# --- 3. The graph of a domain (Т-§8.6, Т-§22.2) ---


class TopicNodeView(BaseModel):
    """A topic on the map: the state is the code and its parameters, never a sentence."""

    topic_id: UUID
    name: str
    status: str
    visibility: str
    display: TopicDisplay
    nearest_due_at_ms: int = 0
    skill_count: int = 0


class DomainGraphResponse(BaseModel):
    """`projected_seq` — how far the graph has read the log; `lag_degraded` — it is behind the
    learner's last event (the map may miss the latest events, Т-§2.6)."""

    domain_id: UUID
    projected_seq: int
    lag_degraded: bool
    topics: list[TopicNodeView]
    requires: list[TopicRequires]


# --- 4. Structured outputs of the roles of stream B (D-A-208); `Curator*` are re-exported ---


class StructurerTopic(BaseModel):
    slug: str
    name: str
    description: str = ""


class StructurerSkill(BaseModel):
    slug: str
    topic_slug: str
    name: str
    description: str = ""


class StructurerPrereq(BaseModel):
    from_topic_slug: str
    to_topic_slug: str


class StructurerSkillDep(BaseModel):
    skill_slug: str
    depends_on_skill_slug: str


class StructurerMisconception(BaseModel):
    skill_slug: str
    name: str
    description: str = ""


class StructurerOutput(BaseModel):
    topics: list[StructurerTopic] = Field(default_factory=list)
    skills: list[StructurerSkill] = Field(default_factory=list)
    prereqs: list[StructurerPrereq] = Field(default_factory=list)
    skill_deps: list[StructurerSkillDep] = Field(default_factory=list)
    misconceptions: list[StructurerMisconception] = Field(default_factory=list)


class DiagnosticOutput(BaseModel):
    prompt_text: str = ""
    options: list[str] = Field(default_factory=list)
    correct_answer: str = ""
    is_correct: bool = False
    score: Unit = 0.0
    tested_depth: Unit = 0.5


class CuratorPromotion(BaseModel):
    skill_id: UUID
    topic_id: UUID
    name: str
    description: str = ""


class CuratorMerge(BaseModel):
    source_id: UUID
    target_id: UUID
    reason: str


class CuratorOutput(BaseModel):
    promotions: list[CuratorPromotion] = Field(default_factory=list)
    skill_merges: list[CuratorMerge] = Field(default_factory=list)
    topic_merges: list[CuratorMerge] = Field(default_factory=list)
    misconception_merges: list[CuratorMerge] = Field(default_factory=list)
