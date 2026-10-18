"""Run the F5 graph on explicitly synthetic input. Never imports candidate DB rows."""

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

from app.core.config import get_settings
from app.integrations.cv_model import ModelExecution, model_version
from app.services.interviews.transcript_preparation import load_fixture
from app.workflows.interview_analysis.graph import build_interview_analysis_graph


async def run(args):
    settings = get_settings()
    execution = ModelExecution(settings.f5_model_provider, settings.f5_model_name,
        settings.f5_model_context_tokens, settings.f5_model_output_tokens, settings.f5_model_timeout_seconds)
    prepared = load_fixture(args.transcript, args.timeline)
    raw = json.loads(args.transcript.read_text(encoding="utf-8"))
    references = json.loads(args.references.read_text(encoding="utf-8"))["references"] if args.references else []
    version = await model_version(execution=execution)
    state = {"prepared": prepared, "criteria": raw["provenance"]["criteria_snapshot"],
             "references": references, "model_options": asdict(execution), "model_version": version,
             "output_directory": str(args.output_directory), "allow_synthetic": True, "group_limit": args.group_limit}
    graph = build_interview_analysis_graph()
    final = await graph.ainvoke(state, {"recursion_limit": 1000})
    artifact = final["artifact"]
    print(json.dumps({"status": artifact["status"], "run_id": artifact["run_id"],
                      "evaluated_groups": artifact["evaluated_groups"], "total_groups": artifact["total_groups"],
                      "error_code": artifact["error_code"], "run_directory": final.get("run_directory"),
                      "synthetic": True, "publishable": False}))
    return 0 if artifact["status"] == "completed_synthetic" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--timeline", type=Path, required=True)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--group-limit", type=int, default=0, help="0 evaluates all groups; a limit produces a partial artifact.")
    args = parser.parse_args()
    if args.group_limit < 0:
        parser.error("group-limit must be nonnegative")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        return asyncio.run(run(args))
    except Exception as error:
        safe_codes = {"MODEL_API_KEY_MISSING", "MODEL_AUTH_FAILED", "MODEL_RATE_LIMITED", "MODEL_NOT_AVAILABLE", "MODEL_REQUEST_INVALID"}
        code = str(error) if isinstance(error, ValueError) and str(error) in safe_codes else "F5_DEVELOPMENT_RUN_FAILED"
        print(json.dumps({"status": "failed", "error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
