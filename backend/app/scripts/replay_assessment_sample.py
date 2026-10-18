"""Replay a compatible saved synthetic run into owned storage without model requests.

Local development CLI only. The common graph revalidates every cached model stage.
No real candidate or transcript can be attached to an imported synthetic sample.
"""
import argparse
import asyncio
import json
import re
import sys
from pathlib import Path
from uuid import UUID

from app.db.session import engine
from app.scripts.assessment_worker import process
from app.services.interviews import assessment_store as store, assessment_runs as runs, analysis
from app.services.interviews.rubric import DIMENSIONS, RUBRIC_VERSION


async def replay(job_id, recruiter_id, directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    artifact = json.loads((directory / "analysis.json").read_text(encoding="utf-8"))
    if (artifact.get("synthetic") is not True or artifact.get("status") != "completed_synthetic"
            or artifact.get("publishable") is not False or artifact.get("f6_ready") is not False
            or manifest.get("synthetic") is not True or manifest.get("prompt") != analysis.PROMPT_VERSION
            or manifest.get("rubric") != RUBRIC_VERSION
            or artifact.get("artifact_sha256") != analysis.digest({k: v for k, v in artifact.items() if k != "artifact_sha256"})):
        raise ValueError("SYNTHETIC_SAMPLE_INCOMPATIBLE")
    run = await runs.start_run(job_id, recruiter_id, runs.StartAssessment(source="synthetic_demo"))
    if run["status"] == "completed":
        return run
    claim = await store.claim(UUID(run["id"]))
    if claim is None:
        raise ValueError("SYNTHETIC_SAMPLE_RUN_NOT_AVAILABLE")
    try:
        inputs = claim["inputs"]
        prepared = inputs["prepared"]
        if (manifest["transcript_digest"] != prepared["aligned_content_sha256"]
                or manifest["source_snapshot"] != prepared["source_snapshot_sha256"]
                or manifest["criteria"] != inputs["criteria"] or manifest["references"] != inputs["references"]
                or manifest["execution"] != inputs["model_options"]):
            raise ValueError("SYNTHETIC_SAMPLE_INPUT_MISMATCH")
        await store.write_stage(claim, "model-version", manifest["model"])
        await store.write_stage(claim, "import-provenance", {"synthetic": True,
            "artifact_sha256": artifact["artifact_sha256"], "source_run_id": artifact["run_id"]})
        for name in ("grouping", "mapping"):
            await store.write_stage(claim, name, json.loads((directory / f"{name}.json").read_text(encoding="utf-8")))
        for group in artifact["groups"]:
            if not re.fullmatch(r"g[1-9][0-9]{0,2}", group["id"]):
                raise ValueError("SYNTHETIC_SAMPLE_GROUP_INVALID")
            for dimension in DIMENSIONS:
                name = f"evaluation-{group['id']}-{dimension}"
                path = directory / f"{name}.json"
                # Explicit no-answer branches are deterministic and have no model file.
                if path.exists():
                    await store.write_stage(claim, name, json.loads(path.read_text(encoding="utf-8")))
        # A missing stage must not silently trigger a fresh API call in this replay CLI.
        from unittest.mock import AsyncMock, patch
        with patch.object(analysis, "generate_json", AsyncMock(side_effect=ValueError("SYNTHETIC_SAMPLE_STAGE_MISSING"))):
            await process(claim)
    except Exception:
        await store.fail(claim, "SYNTHETIC_SAMPLE_REPLAY_FAILED")
        raise
    return await runs.run_status(UUID(run["id"]), recruiter_id)


async def main(args):
    try:
        result = await replay(args.job_id, args.recruiter_id, args.directory)
        print(json.dumps({"id": result["id"], "status": result["status"], "synthetic": True,
            "completed_groups": result["completed_groups"], "error_code": result["error_code"]}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=UUID, required=True)
    parser.add_argument("--recruiter-id", type=UUID, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args))
