"""Read-only recruiter overview; no provider calls or workflow mutations."""
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.auth import DatabaseSession, require_recruiter
from app.models.user import User

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])
Recruiter = Annotated[User, Depends(require_recruiter)]

# Aggregate each stage before joining, so retries and source versions cannot
# multiply applicant counts. All paths start from the recruiter's owned jobs.
OVERVIEW_SQL = """
WITH owned AS (
    SELECT j.id, j.number, j.title, j.created_at, j.profile_version
    FROM job_descriptions j JOIN jd_conversations c ON c.id = j.conversation_id
    WHERE c.recruiter_id = :owner
), latest_screening AS (
    SELECT DISTINCT ON (r.job_id) r.id, r.job_id, r.status
    FROM cv_screening_runs r JOIN owned o ON o.id = r.job_id
    WHERE r.recruiter_id = :owner
    ORDER BY r.job_id, r.created_at DESC, r.id DESC
), screening AS (
    SELECT r.job_id, count(*) FILTER (WHERE a.status = 'completed' AND a.score IS NOT NULL) AS screened,
        count(*) FILTER (WHERE a.status IN ('failed', 'needs_review')) AS issues,
        count(*) FILTER (WHERE a.status = 'completed' AND
            jsonb_array_length(coalesce(a.score->'must_have_review_flags', '[]'::jsonb)) > 0) AS review
    FROM latest_screening r JOIN cv_assessments a ON a.run_id = r.id GROUP BY r.job_id
), applications AS (
    SELECT a.job_description_id AS job_id, count(*) AS applied
    FROM candidate_applications a JOIN owned o ON o.id = a.job_description_id GROUP BY a.job_description_id
), invitations AS (
    SELECT i.job_description_id AS job_id, count(*) AS invited
    FROM interview_invitations i JOIN owned o ON o.id = i.job_description_id
    JOIN interview_invitation_email_jobs e ON e.invitation_id = i.id
    WHERE i.recruiter_id = :owner AND e.status = 'provider_accepted' GROUP BY i.job_description_id
), interviews AS (
    SELECT s.job_description_id AS job_id, count(*) AS interviewed
    FROM interview_sessions s JOIN owned o ON o.id = s.job_description_id
    WHERE s.recruiter_id = :owner AND s.status = 'completed' GROUP BY s.job_description_id
), latest_assessments AS (
    SELECT DISTINCT ON (r.application_id) r.application_id, r.job_id, r.status
    FROM assessment_runs r JOIN owned o ON o.id = r.job_id
    WHERE r.recruiter_id = :owner AND r.kind = 'f6' AND NOT r.synthetic AND r.application_id IS NOT NULL
    ORDER BY r.application_id, r.created_at DESC, r.id DESC
), assessments AS (
    SELECT job_id, count(*) FILTER (WHERE status = 'completed') AS assessed,
        count(*) FILTER (WHERE status IN ('failed', 'needs_review')) AS held
    FROM latest_assessments GROUP BY job_id
)
SELECT o.*, coalesce(a.applied, 0) AS applied, coalesce(s.screened, 0) AS screened,
    coalesce(s.issues, 0) AS issues, coalesce(s.review, 0) AS review,
    coalesce(i.invited, 0) AS invited, coalesce(v.interviewed, 0) AS interviewed,
    coalesce(f.assessed, 0) AS assessed, coalesce(f.held, 0) AS held,
    r.status AS screening_status
FROM owned o LEFT JOIN applications a ON a.job_id = o.id
LEFT JOIN screening s ON s.job_id = o.id LEFT JOIN invitations i ON i.job_id = o.id
LEFT JOIN interviews v ON v.job_id = o.id LEFT JOIN assessments f ON f.job_id = o.id
LEFT JOIN latest_screening r ON r.job_id = o.id
ORDER BY o.created_at DESC, o.id DESC
"""

SESSIONS_SQL = """
SELECT s.id, s.job_description_id AS job_id, s.status, s.starts_at, s.updated_at,
    c.full_name AS name, j.title AS job_title, t.status AS transcript_status
FROM interview_sessions s
JOIN job_descriptions j ON j.id = s.job_description_id
JOIN jd_conversations owner ON owner.id = j.conversation_id AND owner.recruiter_id = :owner
JOIN candidate_applications a ON a.id = s.application_id
JOIN candidates c ON c.id = a.candidate_id
LEFT JOIN LATERAL (
    SELECT status FROM interview_transcripts WHERE session_id = s.id ORDER BY version DESC LIMIT 1
) t ON true
WHERE s.recruiter_id = :owner
ORDER BY s.updated_at DESC, s.id DESC
"""


def build_dashboard(jobs, sessions, source_count, now):
    """Project saved states into labels; unknown scheduling stays unknown."""
    attention, activity = [], []
    for job in jobs:
        job["id"] = str(job["id"])
        waiting = max(0, job["applied"] - job["screened"] - job["issues"])
        job["waiting"] = waiting
        job["status"] = "Profile saved" if job["profile_version"] else "Profile unavailable"
        activity.append({"at": job["created_at"], "text": f'Job #{job["number"]}: {job["title"]} saved',
                         "href": "/recruiter/job-descriptions"})
        for count, label, action, page, priority in (
            (job["issues"], "screening results need review", "Review screening", "cv-screening", 0),
            (job["held"], "assessments need review", "Review assessments", "assessments", 0),
            (job["review"], "candidates have essential-criteria review flags", "Review evidence", "cv-screening", 1),
            (waiting, "applications have no completed screening in the latest run", "Open screening", "cv-screening", 2),
        ):
            if count:
                attention.append({"count": count, "title": f"{count} {label}", "detail": job["title"],
                                  "action": action, "href": f'/recruiter/{page}?job={job["id"]}', "priority": priority})
    for session in sessions:
        session["id"], session["job_id"] = str(session["id"]), str(session["job_id"])
        href = f'/recruiter/interviews?job={session["job_id"]}'
        if session["status"] == "completed" and session["transcript_status"] != "completed":
            attention.append({"count": None, "title": f'{session["name"]}: transcript not ready',
                              "detail": f'{session["job_title"]} · {session["transcript_status"] or "No transcript"}',
                              "action": "Open interview", "href": href, "priority": 0})
        activity.append({"at": session["updated_at"],
                         "text": f'{session["name"]}: interview {session["status"].replace("_", " ")}', "href": href})
    attention.sort(key=lambda item: item["priority"])
    activity.sort(key=lambda item: item["at"], reverse=True)
    active = [s for s in sessions if s["status"] in {"ready", "scheduled", "in_progress", "closing"}]
    active.sort(key=lambda s: (s["status"] != "in_progress", s["starts_at"] is None, s["starts_at"] or now))
    return {"updated_at": now, "jobs": jobs, "attention": attention, "sessions": active[:8],
            "active_session_count": len(active), "activity": activity[:8], "source_count": source_count,
            "totals": {"jobs": len(jobs), "applications": sum(j["applied"] for j in jobs),
                       "waiting": sum(j["waiting"] for j in jobs), "attention": len(attention)}}


@router.get("")
async def dashboard(user: Recruiter, db: DatabaseSession):
    params = {"owner": user.id}
    jobs = [dict(row) for row in (await db.execute(text(OVERVIEW_SQL), params)).mappings()]
    sessions = [dict(row) for row in (await db.execute(text(SESSIONS_SQL), params)).mappings()]
    source_count = (await db.execute(text("""
        SELECT count(*) FROM cv_sources s JOIN job_descriptions j ON j.id = s.job_description_id
        JOIN jd_conversations c ON c.id = j.conversation_id
        WHERE c.recruiter_id = :owner AND s.spreadsheet_id IS NOT NULL AND s.sheet_tab_id IS NOT NULL
    """), params)).scalar_one()
    return build_dashboard(jobs, sessions, source_count, datetime.now(timezone.utc))
