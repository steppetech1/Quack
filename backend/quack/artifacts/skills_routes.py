"""Routes of the learner's skills and the system templates (ТЗ Ф4 WP23.6, §6.4 «Артефакты»;
Т-§17.8, П-11.6; D-C-216).

`GET/POST /v1/learner-skills`, `PATCH/DELETE /v1/learner-skills/{id}` — the learner's own
instructions; `GET /v1/system-templates` — the prompt files of the system, read only.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from quack.api.auth import get_current_learner_id
from quack.api.errors import ApiError
from quack.api.ratelimit import enforce_action_rate_limit
from quack.api.resources import AppResources, get_resources
from quack.artifacts.learner_skills import (
    ARTIFACT_TYPES,
    LearnerSkillRecord,
    SkillConflict,
    SkillNotFound,
    create_skill,
    delete_skill,
    list_skills,
    normalize_name,
    update_skill,
)
from quack.prompts.registry import PromptRegistry

# Router-level auth: 401 before body validation (ТЗ Фазы 3 WP1.2, T-A2).
router = APIRouter(
    prefix="/v1",
    tags=["artifacts"],
    dependencies=[Depends(get_current_learner_id), Depends(enforce_action_rate_limit)],
)

type CurrentLearnerId = Annotated[UUID, Depends(get_current_learner_id)]
type Resources = Annotated[AppResources, Depends(get_resources)]

# Prompt roles whose files the learner may read: the ones that write what the learner sees.
SYSTEM_TEMPLATE_ROLES = ("teacher", "artifact_writer", "artifacts")


class LearnerSkillBindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_types: list[str] = Field(default_factory=list[str])


class LearnerSkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1)
    name: str
    instruction: str
    bindings: LearnerSkillBindings = Field(default_factory=LearnerSkillBindings)


class LearnerSkillPatch(BaseModel):
    """Fields not given stay as they are; a change is a new version."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    instruction: str | None = None
    bindings: LearnerSkillBindings | None = None


class LearnerSkillView(BaseModel):
    skill_id: UUID
    version: int
    name: str
    instruction: str
    bindings: LearnerSkillBindings


class LearnerSkillsResponse(BaseModel):
    skills: list[LearnerSkillView]


class SystemTemplate(BaseModel):
    role: str
    version: int
    sha256: str
    text: str


class SystemTemplatesResponse(BaseModel):
    templates: list[SystemTemplate]


def _view(record: LearnerSkillRecord) -> LearnerSkillView:
    return LearnerSkillView(
        skill_id=record.skill_id,
        version=record.version,
        name=record.name,
        instruction=record.instruction,
        bindings=LearnerSkillBindings(artifact_types=list(record.artifact_types)),
    )


def _invalid(message: str) -> ApiError:
    return ApiError("VALIDATION_ERROR", message, status_code=422)


def _checked_name(name: str) -> str:
    normalized = normalize_name(name)
    if normalized is None:
        raise _invalid("the name is 1-40 letters, digits, '_' or '-'")
    return normalized


def _checked_text(instruction: str, resources: AppResources) -> str:
    text = instruction.strip()
    limit = resources.settings.learner_skill_max_chars
    if not text or len(text) > limit:
        raise _invalid(f"the instruction is 1-{limit} characters")
    return text


def _checked_types(bindings: LearnerSkillBindings) -> tuple[str, ...]:
    unknown = sorted(set(bindings.artifact_types) - set(ARTIFACT_TYPES))
    if unknown:
        raise _invalid(f"unknown artifact types {unknown}; known: {list(ARTIFACT_TYPES)}")
    return tuple(dict.fromkeys(bindings.artifact_types))


def _conflict(exc: SkillConflict) -> ApiError:
    return ApiError("IDEMPOTENCY_CONFLICT", str(exc), status_code=409)


def _not_found(exc: SkillNotFound) -> ApiError:
    return ApiError("NOT_FOUND", str(exc), status_code=404)


@router.get("/learner-skills", response_model=LearnerSkillsResponse)
async def get_learner_skills(
    learner_id: CurrentLearnerId, resources: Resources
) -> LearnerSkillsResponse:
    records = await list_skills(resources.db, learner_id=learner_id)
    return LearnerSkillsResponse(skills=[_view(record) for record in records])


@router.post("/learner-skills", response_model=LearnerSkillView, status_code=201)
async def post_learner_skill(
    body: LearnerSkillCreate, learner_id: CurrentLearnerId, resources: Resources
) -> LearnerSkillView:
    try:
        record = await create_skill(
            resources.db,
            learner_id=learner_id,
            idempotency_key=body.idempotency_key,
            name=_checked_name(body.name),
            instruction=_checked_text(body.instruction, resources),
            artifact_types=_checked_types(body.bindings),
        )
    except SkillConflict as exc:
        raise _conflict(exc) from exc
    return _view(record)


@router.patch("/learner-skills/{skill_id}", response_model=LearnerSkillView)
async def patch_learner_skill(
    skill_id: UUID, body: LearnerSkillPatch, learner_id: CurrentLearnerId, resources: Resources
) -> LearnerSkillView:
    try:
        record = await update_skill(
            resources.db,
            learner_id=learner_id,
            skill_id=skill_id,
            name=None if body.name is None else _checked_name(body.name),
            instruction=None
            if body.instruction is None
            else _checked_text(body.instruction, resources),
            artifact_types=None if body.bindings is None else _checked_types(body.bindings),
        )
    except SkillNotFound as exc:
        raise _not_found(exc) from exc
    except SkillConflict as exc:
        raise _conflict(exc) from exc
    return _view(record)


@router.delete("/learner-skills/{skill_id}", response_model=LearnerSkillsResponse)
async def remove_learner_skill(
    skill_id: UUID, learner_id: CurrentLearnerId, resources: Resources
) -> LearnerSkillsResponse:
    """The skill with all its versions; the answer is the skills that are left."""
    try:
        await delete_skill(resources.db, learner_id=learner_id, skill_id=skill_id)
    except SkillNotFound as exc:
        raise _not_found(exc) from exc
    return await get_learner_skills(learner_id, resources)


@router.get("/system-templates", response_model=SystemTemplatesResponse)
async def get_system_templates() -> SystemTemplatesResponse:
    """The prompt files of the system with their versions (Т-§11.6), as they are."""
    registry = PromptRegistry()
    found: list[SystemTemplate] = []
    refs = sorted((r.partition("@")[0], int(r.partition("@")[2])) for r in registry.manifest())
    for role, version in refs:
        if role not in SYSTEM_TEMPLATE_ROLES:
            continue
        template = registry.load(role, version)
        found.append(
            SystemTemplate(
                role=role, version=template.version, sha256=template.sha256, text=template.text
            )
        )
    return SystemTemplatesResponse(templates=found)
