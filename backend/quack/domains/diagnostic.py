"""Adaptive diagnostics over the domain DAG and propagation of `inferred_priors`
upward and downward along prerequisite edges (ТЗ §2.3, §3, §4; Т-§3.3).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from quack.api.app import AppResources
from quack.config import Settings
from quack.domains.schemas import (
    AnswerDiagnosticRequest,
    AnswerDiagnosticResponse,
    FinishDiagnosticRequest,
    FinishDiagnosticResponse,
    PrereqEdge,
    SkillDepEdge,
    StartDiagnosticRequest,
    StartDiagnosticResponse,
    TopicPriorItem,
)
from quack.llm.gateway import LLMGateway

DIAGNOSTIC_MAX_QUESTIONS = 5
PRIOR_TESTED_FACTOR = 0.5
PRIOR_TESTED_CONFIDENCE = 0.5
PRIOR_PROPAGATED_SUCCESS_THRESHOLD = 0.7
PRIOR_PROPAGATED_FAIL_THRESHOLD = 0.5
PRIOR_PROPAGATED_DECAY = 0.85
PRIOR_DOWN_BASE_MASTERY = 0.4
PRIOR_DOWN_BASE_CONFIDENCE = 0.35
PRIOR_UP_FAIL_MASTERY = 0.05
PRIOR_UP_BASE_CONFIDENCE = 0.4


def compute_inferred_priors(
    skills: Sequence[tuple[UUID, UUID]],
    skill_deps: Sequence[SkillDepEdge],
    topic_prereqs: Sequence[PrereqEdge],
    tested_scores: dict[UUID, float],
) -> list[TopicPriorItem]:
    """Compute `TopicPriorItem` list for domain skills from tested scores, propagating
    downward to prerequisites on success (`score >= 0.7`) and upward to dependents on failure
    (`score < 0.5`) (ТЗ §2.3, Т-§3.3).
    """
    raise NotImplementedError


async def start_diagnostic(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: StartDiagnosticRequest,
) -> StartDiagnosticResponse:
    """Select mid-DAG high-hub candidate topics, generate first diagnostic question
    (`task.generated`), and emit `diagnostic.started` (ТЗ §2.3, §3; Т-§3.3).
    """
    raise NotImplementedError


async def answer_diagnostic(
    resources: AppResources,
    settings: Settings,
    llm: LLMGateway,
    learner_id: UUID,
    domain_id: UUID,
    req: AnswerDiagnosticRequest,
) -> AnswerDiagnosticResponse:
    """Evaluate diagnostic answer, emit `diagnostic.question_answered`, and adaptively select
    the next question upward (if correct) or downward (if incorrect) along the DAG up to
    `DIAGNOSTIC_MAX_QUESTIONS` (ТЗ §2.3, §3; Т-§3.3).
    """
    raise NotImplementedError


async def finish_diagnostic(
    resources: AppResources,
    settings: Settings,
    learner_id: UUID,
    domain_id: UUID,
    req: FinishDiagnosticRequest,
) -> FinishDiagnosticResponse:
    """Compute `inferred_priors` across the DAG and emit `diagnostic.completed`
    (ТЗ §2.3, §3; Т-§3.3).
    """
    raise NotImplementedError
