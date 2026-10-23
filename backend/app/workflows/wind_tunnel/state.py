"""Checkpoint state contains run/stage references, not CV prose or expectations."""
from typing import TypedDict


class AnalysisState(TypedDict, total=False):
    run_id: str
    stage: str
    stop_dispatch: bool
    reuse_analysis: bool
    suggestion_status: str
