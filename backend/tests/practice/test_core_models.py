"""Q-A-1 variant (a), seam S3 (ТЗ Ф4 WP2.1, §4.1): payload models that stream B also uses live
in the core (`quack.events.values`); `quack.practice` re-exports them — the same objects as in
the event registry, not copies. Models with labels (model outputs) keep the fields of their
id form (`quack.events.records`) except the label fields."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from quack.agents.schemas import ExtractedElement
from quack.events import values
from quack.events.records import ExtractedElementRecord, TaskMarkupRecord, TurnMarkupRecord
from quack.events.registry import payload_model
from quack.practice import answer_spec, check, markup
from quack.practice.markup import TaskMarkup
from quack.session.teacher_output import TurnMarkup

SHARED = [
    (answer_spec, "MathValue"),
    (answer_spec, "Variable"),
    (answer_spec, "AnswerOption"),
    (answer_spec, "AnswerSpec"),
    (answer_spec, "LearnerAnswer"),
    (check, "SecondOpinion"),
    (check, "RubricItemResult"),
    (check, "Verdict"),
    (check, "CheckFailed"),
    (markup, "NewSkill"),
]


@pytest.mark.parametrize(("module", "name"), SHARED, ids=[name for _, name in SHARED])
def test_practice_models_are_the_core_objects(module: object, name: str) -> None:
    assert getattr(module, name) is getattr(values, name)


def test_registered_payloads_are_the_practice_models() -> None:
    assert payload_model("verdict.recorded") is check.Verdict
    assert payload_model("check.failed") is check.CheckFailed


# label field of the model output → id field of the record; fields only the record has
LABEL_FORMS: list[tuple[type[BaseModel], type[BaseModel], dict[str, str], set[str]]] = [
    (TaskMarkup, TaskMarkupRecord, {}, set()),
    (TurnMarkup, TurnMarkupRecord, {"targets_misconception": "targets_misconception_id"}, set()),
    (
        ExtractedElement,
        ExtractedElementRecord,
        {"skill": "skill_id", "misconception": "misconception_id"},
        {"at_ms"},
    ),
]


@pytest.mark.parametrize(
    ("labeled", "record", "label_to_id", "record_only"),
    LABEL_FORMS,
    ids=[labeled.__name__ for labeled, *_ in LABEL_FORMS],
)
def test_label_form_has_the_fields_of_the_id_form(
    labeled: type[BaseModel],
    record: type[BaseModel],
    label_to_id: dict[str, str],
    record_only: set[str],
) -> None:
    mapped = {label_to_id.get(name, name) for name in labeled.model_fields}
    assert mapped == set(record.model_fields) - record_only
