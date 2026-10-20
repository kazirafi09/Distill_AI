"""Graph state; validated model output contracts live in services/interviews."""

from typing import TypedDict


class InterviewAnalysisState(TypedDict, total=False):
    storage_run_id: str
    storage_token: str
    prepared: dict
    criteria: list[dict]
    references: list[dict]
    model_options: dict
    model_version: dict
    output_directory: str
    allow_synthetic: bool
    group_limit: int
    run_id: str
    run_directory: str
    status: str
    error_code: str | None
    turns: list[dict]
    groups: list[dict]
    excluded_turns: list[dict]
    group_index: int
    relevance: dict | None
    accuracy: dict | None
    completeness: dict | None
    articulation: dict | None
    merged_evaluation: dict
    results: list[dict]
    coverage: list[dict]
    artifact: dict
    recovery: dict
