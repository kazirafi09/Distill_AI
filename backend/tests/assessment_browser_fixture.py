"""Disposable local UI fixture using previously verified synthetic Gemini stages."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select
from app.core.security import hash_password
from app.db.session import SessionFactory, engine
from app.models.user import User
from app.models.assessments import AssessmentStage
from app.scripts.replay_assessment_sample import replay
from test_assessment_storage import AssessmentDatabaseTests

ROOT = Path(__file__).resolve().parents[2]
RECORD = ROOT / "storage/diagnostics/assessment-browser-fixture.json"
PASSWORD = "Synthetic-UI-Only-2026!"


async def main(mode, f6_source=None):
    if mode == "cleanup":
        if RECORD.exists():
            record = json.loads(RECORD.read_text(encoding="utf-8"))
            async with SessionFactory() as db:
                await db.execute(delete(User).where(User.id.in_([UUID(item) for item in record["user_ids"]])))
                await db.commit()
            RECORD.unlink()
        await engine.dispose()
        return
    fixture = AssessmentDatabaseTests()
    await fixture.asyncSetUp()
    try:
        async with SessionFactory() as db:
            owner = await db.get(User, fixture.owner)
            owner.email = f"assessment-ui-{fixture.owner}@example.com"
            owner.password_hash = hash_password(PASSWORD)
            email = owner.email
            await db.commit()
        run = await replay(fixture.job, fixture.owner, ROOT / "storage/diagnostics/f5-scoring-calibration/3501e358fc65896d249fb25c9bb503f2a8098e266a6a1da0e3944c7884572c6d")
        record = {"user_ids": [str(fixture.owner), str(fixture.other)], "job_id": str(fixture.job), "run_id": run["id"]}
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps(record), encoding="utf-8")
        if f6_source:
            from app.services.interviews import consolidation_runs, assessment_store
            from app.scripts.assessment_worker import process
            child = await consolidation_runs.start(UUID(run["id"]), fixture.owner, consolidation_runs.StartConsolidation())
            claim = await assessment_store.claim(UUID(child["id"]))
            async with SessionFactory() as db:
                stages = list(await db.scalars(select(AssessmentStage).where(AssessmentStage.run_id == f6_source)))
            for stage in stages:
                if stage.name == "model-version" or stage.name.startswith("f6-"):
                    await assessment_store.write_stage(claim, stage.name, stage.payload)
            await process(claim)
            record["f6_run_id"] = child["id"]
            RECORD.write_text(json.dumps(record), encoding="utf-8")
        print(json.dumps({"email": email, "password": PASSWORD, **record, "status": run["status"]}))
    finally:
        fixture.generator.stop()
        fixture.version.stop()
        fixture.model.tmp.cleanup()
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("seed", "cleanup"))
    parser.add_argument("--f6-source", type=UUID)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    args = parser.parse_args()
    asyncio.run(main(args.mode, args.f6_source))
