from typing import TypedDict


class ConsolidationState(TypedDict, total=False):
    storage_run_id: str
    storage_token: str
    inputs: dict
    model_version: dict
    sources: list[dict]
    strengths: dict
    weaknesses: dict
    evidence_gaps: dict
    recommended_areas: dict
    draft: dict
    validated_draft: dict
    repair_count: int
    validation_error: str | None
    validation_details: dict
    status: str
    error_code: str | None
    artifact: dict
