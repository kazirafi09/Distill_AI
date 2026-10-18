"""Disposable live browser fixtures. Pipe into the running backend container.

Modes: create, check, change, cleanup. Real APIs, model-version discovery and DB;
seeded synthetic assessments, no AI inference and no real candidate data.
"""
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import delete, select
from app.core.security import create_access_token, hash_password
from app.db.session import SessionFactory, engine
from app.models.user import User
from app.models.jd import JDConversation, JobDescription
from app.models.cv import Candidate, CandidateApplication, CVSource
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.services.cv.screening import inputs_for, digest, commit_screening_write
from app.services.cv.scoring import calculate_scores
from app.services.interviews.selections import load_shortlist
import httpx

MARKER = Path("/workspace/storage/cvs/.shortlist-browser-fixture.json")
PASSWORD = "Disposable-Shortlist-2026!"
PROFILE = {
    "criteria": [{"id": "python", "text": "Python", "dimension": "technical_skills",
                  "priority": "Must have", "source_quote": "Python", "priority_quote": "Must have"}],
    "addressed_dimensions": ["technical_skills", "work_experience", "qualifications", "additional_criteria"],
    "minimum_years_experience": None, "fresher_eligibility": None,
    "dimension_weights": {"technical_skills": 100, "work_experience": 0, "qualifications": 0, "additional_criteria": 0},
    "weights_confirmed": True, "weights_confirmation_quote": "Technical skills 100%",
}


async def main():
    mode = sys.argv[1]
    if mode == "create":
        if MARKER.exists():
            raise RuntimeError("Existing fixture must be cleaned up first")
        data = {"users": [str(uuid4()), str(uuid4())], "jobs": [], "applications": []}
        MARKER.write_text(json.dumps(data))
        async with SessionFactory() as db:
            for identifier in data["users"]:
                db.add(User(id=UUID(identifier), email=f"shortlist-browser-{identifier}@example.com",
                            password_hash=hash_password(PASSWORD), role="recruiter"))
            await db.flush()
            for job_index in range(3):
                owner = UUID(data["users"][0 if job_index < 2 else 1])
                conversation = JDConversation(recruiter_id=owner)
                db.add(conversation)
                await db.flush()
                job = JobDescription(conversation_id=conversation.id,
                                     title=f"Shortlist live QA {job_index + 1}", content="Synthetic Python role",
                                     requirements="Python Must have", screening_profile=PROFILE, profile_version=1)
                db.add(job)
                await db.flush()
                data["jobs"].append(str(job.id))
                source = CVSource(job_description_id=job.id, drive_folder_id=f"shortlist-browser-{job.id}")
                db.add(source)
                await db.flush()
                applications = []
                names = ("QA Alice", "QA Bob", "QA Carol", "QA Missing score", "QA Invalid score", "QA Never assessed") if job_index == 0 else ("QA Other job",)
                for index, name in enumerate(names):
                    candidate = Candidate(recruiter_id=owner, full_name=name,
                                          email=f"qa-{index}-{job.id}@example.invalid")
                    db.add(candidate)
                    await db.flush()
                    application = CandidateApplication(candidate_id=candidate.id, job_description_id=job.id,
                                                       source_id=source.id, source_row_number=index + 2)
                    db.add(application)
                    await db.flush()
                    applications.append(application)
                    data["applications"].append(str(application.id))
                inputs = await inputs_for(db, job, owner)
                run = ScreeningRun(job_id=job.id, recruiter_id=owner, inputs=inputs,
                                   input_key=digest(inputs), status="completed")
                db.add(run)
                await db.flush()
                for index, application in enumerate(applications):
                    if index == 5:
                        continue
                    decision = ("met", "partially_met", "unmet")[min(index, 2)]
                    matches = [{"requirement_id": "python", "decision": decision,
                                "justification": "Synthetic evidence for live shortlist QA", "unsupported_requirement_parts": [],
                                "evidence": [{"quote": "Synthetic Python project", "source_id": "synthetic_page",
                                              "file_version_id": "synthetic", "ocr_derived": False}]}]
                    score = calculate_scores(PROFILE, matches)
                    if index == 3:
                        score = None
                    if index == 4:
                        score = {"aggregate": "NaN"}
                    db.add(CVAssessment(run_id=run.id, application_id=application.id, status="completed",
                                        file_version_ids=[], score=score, matches=matches if index < 3 else []))
                if job_index == 0:
                    data["run"] = str(run.id)
                    data["jd_number"] = job.number
            await db.commit()
        MARKER.write_text(json.dumps(data))
        print(json.dumps({"email": f"shortlist-browser-{data['users'][0]}@example.com",
                          "password": PASSWORD, "jobs": data["jobs"], "jd_number": data["jd_number"]}))
    elif mode == "cleanup":
        data = json.loads(MARKER.read_text())
        async with SessionFactory() as db:
            await db.execute(delete(User).where(User.id.in_([UUID(value) for value in data["users"]])))
            await db.commit()
        MARKER.unlink()
        print("Disposable browser fixtures removed")
    else:
        data = json.loads(MARKER.read_text())
        job_id, owner = UUID(data["jobs"][0]), UUID(data["users"][0])
        if mode == "change":
            async with SessionFactory() as db:
                assessment = await db.scalar(select(CVAssessment).where(
                    CVAssessment.run_id == UUID(data["run"]),
                    CVAssessment.application_id == UUID(data["applications"][0])))
                assessment.score = {**assessment.score, "aggregate": "90"}
                await commit_screening_write(db, job_id)
            print("Synthetic assessment changed")
        elif mode == "check":
            loaded = await load_shortlist(job_id, owner)
            print(json.dumps({"saved": loaded}))
            async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
                other_token = create_access_token(UUID(data["users"][1]))
                denied = await client.post(f"/interviews/jobs/{job_id}/shortlist/preview",
                                          headers={"Authorization": f"Bearer {other_token}"}, json={"mode": "top_k", "k": 2})
                assert denied.status_code == 404
                unauth = await client.get(f"/interviews/jobs/{job_id}/shortlist")
                assert unauth.status_code == 401
            print("Live HTTP ownership and authentication checks passed")
        else:
            raise ValueError("Unknown mode")
    await engine.dispose()


asyncio.run(main())
