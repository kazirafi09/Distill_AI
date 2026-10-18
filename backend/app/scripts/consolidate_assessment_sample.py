"""Run F6 for an existing synthetic F5 sample; never accepts real candidate runs."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

from app.db.session import SessionFactory, engine
from app.models.assessments import AssessmentRun
from app.scripts.assessment_worker import run_once
from app.services.interviews.consolidation_runs import start, StartConsolidation
from app.services.interviews.assessment_runs import run_result


async def main(parent_id):
    try:
        async with SessionFactory() as db:
            parent = await db.get(AssessmentRun, parent_id)
            if not parent or not parent.synthetic:
                raise ValueError("An existing synthetic F5 run is required.")
            owner = parent.recruiter_id
        child = await start(parent_id, owner, StartConsolidation())
        print(json.dumps({"id": child["id"], "status": child["status"]}), flush=True)
        await run_once(UUID(child["id"]))
        result = await run_result(UUID(child["id"]), owner)
        destination = Path(__file__).resolve().parents[3] / "storage/diagnostics/f6-consolidation" / child["id"] / "assessment.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result["result"], indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"id": child["id"], "status": result["run"]["status"],
            "error_code": result["run"]["error_code"], "output": str(destination)}), flush=True)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("f5_run_id", type=UUID)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(parser.parse_args().f5_run_id))
