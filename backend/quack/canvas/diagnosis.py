"""Quick diagnosis of a canvas line and the `canvas_diagnoser@1` schema (ТЗ Ф4 WP21.4 п.3, S8;
Т-§15.4, Т-§7.1).

The diagnoser (Spark, P0) runs when the symbolic check of a transition is impossible or the line
fell into a wrong branch of the solution space. It gets the problem, the solution space and the
earlier lines and says whether the new line holds an error and, for an error, the gap type, the
misconception and the root-cause skill (Т-§7.1). Misconceptions and skills are short labels
(`M1`, `S2`, Т-§4.5) that code translates to ids. The schema lives in the package that calls the
role (§7.2 S8); `tests/llm/prompt_contracts.py` registers it (D-C-136).

Only a found error becomes `observation.diagnosed` (D-C-120): `kind = question` when the
confidence is below `mark_min_confidence` (Т-§12.4, WP21.5), otherwise `error`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quack.config import Settings
from quack.events.records import DiagnosisRecord
from quack.events.values import GapType
from quack.llm.labels import LabelMap


class CanvasDiagnoserOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: bool
    gap_type: GapType
    misconception: str | None
    misconception_hypothesis: str | None
    root_cause: str | None
    confidence: float = Field(ge=0.0, le=1.0)


type DiagnosisMethod = Literal["model", "symbolic"]


def diagnosis_record(
    output: CanvasDiagnoserOutput, labels: LabelMap, settings: Settings
) -> tuple[DiagnosisRecord, float] | None:
    """The diagnosis of `observation.diagnosed` with its confidence, or `None` when the line
    holds no error (D-C-120). An unknown label raises `UnknownLabelError`: a labelling error is
    returned to the model by the gateway (WP14.1). `gap_type = none` with an error found is no
    diagnosis of an error and reads as none (D-C-136)."""
    if not output.error or output.gap_type == "none":
        return None
    misconception = (
        labels.resolve("misconception", output.misconception) if output.misconception else None
    )
    root_cause = labels.resolve("skill", output.root_cause) if output.root_cause else None
    kind: Literal["error", "question"] = (
        "question" if output.confidence < settings.mark_min_confidence else "error"
    )
    record = DiagnosisRecord(
        kind=kind,
        gap_type=output.gap_type,
        misconception_id=misconception,
        misconception_hypothesis=None if misconception else output.misconception_hypothesis,
        root_cause_skill_id=root_cause,
    )
    return record, output.confidence
