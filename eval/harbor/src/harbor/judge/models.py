"""Pydantic models mirroring the dimension_scores.json schema.

All fields are optional/tolerant: the judge agent writes the file, and the
pipeline backfills passed/applicable/dimension_score before validation.
"""

from pydantic import BaseModel, ConfigDict, Field


class JudgeItemResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str | None = None
    verdict: int | str | None = Field(default=None, alias="pass")
    evidence: str | None = None
    hard_check_refs: list[str] | None = None


class JudgeDimensionScore(BaseModel):
    dimension: str | None = None
    items: list[JudgeItemResult] = Field(default_factory=list)
    passed: int | None = None
    applicable: int | None = None
    dimension_score: float | None = None


class SimulatorCheckResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    id: str | None = None
    verdict: int | str | None = Field(default=None, alias="pass")
    evidence: str | None = None


class SimulatorEvaluation(BaseModel):
    ok: bool | None = None
    checks: list[SimulatorCheckResult] = Field(default_factory=list)
    contaminated_dimensions: list[str] = Field(default_factory=list)
    note: str | None = None


class JudgeResult(BaseModel):
    instance_id: str | None = None
    scores: list[JudgeDimensionScore] = Field(default_factory=list)
    simulator_evaluation: SimulatorEvaluation | None = None
    notes: str | None = None
