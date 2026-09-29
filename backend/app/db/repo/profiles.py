"""Profile persistence and deterministic readiness calculation."""

import re
from datetime import UTC, datetime
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Profile as ProfileRow
from app.errors import ValidationFailed
from app.schemas.profile import Profile, ProfileUpdateIn, Questionnaire, Traits

QUESTIONNAIRE_PATHS = frozenset(
    {
        "level.grade",
        "level.admission_year",
        "direction.field",
        "direction.alternatives",
        "academics.self_assessment",
        "academics.ent_trial_score",
        "academics.ent_profile_pair",
        "academics.sat_score",
        "academics.sat_target",
        "academics.sat_date",
        "academics.ent_target",
        "academics.ent_date",
        "academics.ielts_score",
        "academics.ielts_target",
        "preferences.countries",
        "preferences.cities",
        "preferences.language",
        "preferences.budget_per_year",
        "preferences.currency",
        "preferences.grant_need",
        "constraints.required",
        "constraints.excluded",
        "priorities.ranking",
        "pace.hours_per_week",
        "pace.explanation_depth",
        "pace.hint_level",
    }
)


def profile_readiness(questionnaire: Questionnaire) -> float:
    def filled(path: str) -> bool:
        section, leaf = path.split(".")
        return getattr(getattr(questionnaire, section), leaf).value is not None

    weights = (
        ("direction.field", 0.20),
        ("preferences.budget_per_year", 0.15),
        ("preferences.grant_need", 0.15),
        ("preferences.countries", 0.15),
        ("level.grade", 0.10),
        ("academics.ielts_score", 0.05),
        ("preferences.language", 0.05),
        ("pace.hours_per_week", 0.05),
    )
    score = sum(weight for path, weight in weights if filled(path))
    if filled("academics.sat_score") or filled("academics.ent_trial_score"):
        score += 0.10
    return round(score, 10)


async def get_profile(session: AsyncSession, student_id: UUID) -> Profile:
    row = await session.get(ProfileRow, student_id)
    if row is None:
        return Profile(student_id=student_id)
    questionnaire = Questionnaire.model_validate(row.questionnaire)
    return Profile(
        student_id=student_id,
        questionnaire=questionnaire,
        traits=Traits.model_validate(row.traits),
        readiness=profile_readiness(questionnaire),
    )


# What a number in a slot can honestly be. A model that hears «100 тыс в месяц» and
# writes it into a score is not caught by the type: the range is the check.
# Outside it the value is refused, with a message that says what to do.
NUMBER_BOUNDS: dict[str, tuple[float, float]] = {
    "academics.ent_trial_score": (0, 140),
    "academics.ent_target": (0, 140),
    "academics.sat_score": (0, 1600),
    "academics.sat_target": (0, 1600),
    "academics.ielts_score": (0, 9),
    "academics.ielts_target": (0, 9),
    "level.grade": (1, 12),
    "level.admission_year": (2000, 2100),
    "pace.hours_per_week": (0, 100),
    "preferences.budget_per_year": (0, 10**9),
}


def check_number_bounds(path: str, value: object) -> None:
    bounds = NUMBER_BOUNDS.get(path)
    if bounds is None or isinstance(value, bool) or not isinstance(value, int | float):
        return
    low, high = bounds
    if not low <= value <= high:
        raise ValidationFailed(
            f"{path}: {value} is outside {low:g}..{high:g}; probably another fact "
            "(money, hours, a year). Do not store it here: ask the student."
        )


async def apply_profile_update(
    session: AsyncSession, student_id: UUID, upd: ProfileUpdateIn
) -> Profile:
    if upd.path not in QUESTIONNAIRE_PATHS | {"traits.summary", "traits.verbatim"}:
        raise ValidationFailed("unknown profile path")

    profile = await get_profile(session, student_id)
    now = datetime.now(UTC)
    try:
        if upd.path == "traits.verbatim":
            value = TypeAdapter(str).validate_python(upd.value)
            profile.traits.verbatim.append(value)
        elif upd.path == "traits.summary":
            profile.traits.summary = TypeAdapter(str).validate_python(upd.value)
        else:
            section_name, leaf_name = upd.path.split(".")
            field = getattr(getattr(profile.questionnaire, section_name), leaf_name)
            raw_val = upd.value
            if upd.path == "preferences.grant_need":
                if isinstance(raw_val, bool):
                    raw_val = "preferred" if raw_val else "not_needed"
                elif isinstance(raw_val, str):
                    v = raw_val.lower().strip()
                    if v in (
                        "true",
                        "yes",
                        "need",
                        "grant",
                        "желательно",
                        "нужен",
                        "хотелось бы",
                        "хочу",
                    ):
                        raw_val = "preferred"
                    elif v in ("false", "no", "not_needed", "не нужен", "нет"):
                        raw_val = "not_needed"
                    elif v in ("only_grant", "только", "только грант"):
                        raw_val = "only_grant"
            elif upd.path == "level.grade" and isinstance(raw_val, str):
                digits = re.findall(r"\d+", raw_val)
                if digits:
                    raw_val = int(digits[0])
            check_number_bounds(upd.path, raw_val)
            value = TypeAdapter(
                field.__class__.model_fields["value"].annotation
            ).validate_python(raw_val)
            field.value = value
            field.mark = "stated" if upd.by == "user" else "assumed"
            field.updated_at = now
    except ValidationError as error:
        detail = "; ".join(
            f"{e['loc'][0] if e.get('loc') else upd.path}: {e.get('msg', '')}"
            for e in error.errors()
        )
        raise ValidationFailed(f"invalid profile value: {detail}") from error

    profile.readiness = profile_readiness(profile.questionnaire)
    row = await session.get(ProfileRow, student_id)
    data = {
        "questionnaire": profile.questionnaire.model_dump(mode="json"),
        "traits": profile.traits.model_dump(mode="json"),
        "updated_at": now,
    }
    if row is None:
        session.add(ProfileRow(student_id=student_id, **data))
    else:
        for key, value in data.items():
            setattr(row, key, value)
    await session.flush()
    return profile
