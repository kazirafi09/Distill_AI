# Session and LiveKit API — proposed code for review

13 October 2026 (Asia/Dhaka). User approved this proposal; source changes are now implemented. Migration d7f1b9036a42 is prepared but not applied by the agent. Syntax/model metadata and offline upgrade/downgrade SQL checks passed. SDK/database/API test execution is pending the user's commands. The code below is the reviewed design; see DAILY_WORK_2026-10-13.md for execution details.

## Understanding

The job-owning recruiter conducts the interview. There is no schedule or interviewer assignment. Each selected application has at most one session and one current invitation. The candidate checks their device, waits, and joins only after recruiter admission. Recruiter Start returns the host's room token; candidate Join returns a token for the same room. Leave/reconnect keeps the session alive. Only recruiter End completes it.

Use existing SessionFactory, owned_application(), job transaction lock, shortlist tables and invitation token hash. Tokens are signed while the job lock is held so admission/end cannot race token issuance. Room deletion is outside the database transaction; closing is persisted first, blocks tokens, and allows retry after failure. Actual room/media presence is not inferred from a token or started_at: started_at records the recruiter Start action. UI/LiveKit events will show actual connection state.

This checkpoint implements the application authorization/lifecycle contract for local testing. It does not claim live presence, duplicate-tab takeover protection, rate limiting, webhook reconciliation, recording or strict self-hosted token revocation; those remain explicit acceptance work before a production release.

## 1. Changes to models/interviews.py

Add closing to the existing status check:

```python
"status IN ('ready', 'scheduled', 'in_progress', 'closing', 'completed', 'cancelled')"
```

Add these fields after started_at:

```python
candidate_admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
admitted_invitation_hash: Mapped[str | None] = mapped_column(String(64))
```

Admission is tied to the current invitation hash. Reissued links require fresh admission; never put this hash in API responses. Existing invitation hashing remains unchanged.

## 2. New migration after c6e9a2048f73

```python
"""Add admission state and retryable interview closure."""
from alembic import op
import sqlalchemy as sa

revision = "d7f1b9036a42"
down_revision = "c6e9a2048f73"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("interview_sessions", sa.Column("candidate_admitted_at", sa.DateTime(timezone=True)))
    op.add_column("interview_sessions", sa.Column("admitted_invitation_hash", sa.String(64)))
    op.drop_constraint("ck_interview_session_status", "interview_sessions", type_="check")
    op.create_check_constraint(
        "ck_interview_session_status", "interview_sessions",
        "status IN ('ready', 'scheduled', 'in_progress', 'closing', 'completed', 'cancelled')",
    )


def downgrade():
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM interview_sessions WHERE status = 'closing') THEN
                RAISE EXCEPTION 'Finish pending room closures before downgrading';
            END IF;
        END $$;
    """)
    op.drop_constraint("ck_interview_session_status", "interview_sessions", type_="check")
    op.create_check_constraint(
        "ck_interview_session_status", "interview_sessions",
        "status IN ('ready', 'scheduled', 'in_progress', 'completed', 'cancelled')",
    )
    op.drop_column("interview_sessions", "admitted_invitation_hash")
    op.drop_column("interview_sessions", "candidate_admitted_at")
```

Downgrade removes admission data and must only be used for an intentional rollback. Do not run it during normal verification.

## 3. Addition to services/interviews/invitations.py

Extract shared validation so candidate status and joining use the same expiry/revocation rules. Add this helper:

```python
async def valid_invitation(db, token: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise HTTPException(410, "This interview link is invalid or no longer active.")

    invitation = await db.scalar(
        select(InterviewInvitation)
        .where(InterviewInvitation.token_hash == token_hash(token))
        .execution_options(populate_existing=True)
    )
    if (
        invitation is None
        or invitation.revoked_at is not None
        or invitation.expires_at <= datetime.now(timezone.utc)
    ):
        raise HTTPException(410, "This interview link is invalid or no longer active.")
    return invitation
```

Existing validate_entry() will use this helper inside its existing session, then fetch the job title and return its existing response shape. Keep interview_ready=False until the UI is updated to use the new candidate status endpoint. That flag must not imply authorization to join.

## 4. New services/interviews/sessions.py

```python
"""Recruiter-owned sessions, candidate admission and LiveKit access."""

from datetime import datetime, timezone
from uuid import uuid4

from aiohttp import ClientError
from fastapi import HTTPException
from livekit import api
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.interviews import (
    InterviewInvitation, InterviewSession,
    InterviewShortlist, InterviewShortlistMember,
)
from app.services.cv.screening import lock_screening_job, owned_application
from app.services.interviews.invitations import valid_invitation
from app.services.interviews.livekit import create_join_token, close_room


def session_details(session):
    return {
        "id": str(session.id),
        "application_id": str(session.application_id),
        "status": session.status,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
    }


async def owned_session(db, job_id, application_id, recruiter_id):
    await lock_screening_job(db, job_id)
    await owned_application(db, job_id, application_id, recruiter_id)
    return await db.scalar(
        select(InterviewSession).where(
            InterviewSession.application_id == application_id,
            InterviewSession.job_description_id == job_id,
            InterviewSession.recruiter_id == recruiter_id,
        ).execution_options(populate_existing=True)
    )


async def active_invitation(db, job_id, application_id, recruiter_id):
    invitation = await db.scalar(select(InterviewInvitation).where(
        InterviewInvitation.application_id == application_id,
        InterviewInvitation.job_description_id == job_id,
        InterviewInvitation.recruiter_id == recruiter_id,
        InterviewInvitation.revoked_at.is_(None),
        InterviewInvitation.expires_at > datetime.now(timezone.utc),
    ))
    if invitation is None:
        raise HTTPException(409, "An active candidate invitation is required.")
    return invitation


def require_active(session):
    if session is None:
        raise HTTPException(404, "Interview session not found.")
    if session.status != "in_progress":
        raise HTTPException(409, "This interview is not active.")


async def start_interview(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        member_id = await db.scalar(
            select(InterviewShortlistMember.id)
            .join(InterviewShortlist)
            .where(
                InterviewShortlistMember.application_id == application_id,
                InterviewShortlist.job_description_id == job_id,
                InterviewShortlist.recruiter_id == recruiter_id,
            )
        )
        if member_id is None:
            raise HTTPException(409, "Candidate must be in this job's saved selection.")
        await active_invitation(db, job_id, application_id, recruiter_id)

        if session is None:
            session = InterviewSession(
                id=uuid4(), application_id=application_id,
                job_description_id=job_id, recruiter_id=recruiter_id,
                room_name=f"interview-{uuid4()}", status="ready", version=1,
            )
            db.add(session)
        if session.status not in {"ready", "in_progress"}:
            raise HTTPException(409, "This interview cannot be started.")

        token = create_join_token(session.room_name, f"recruiter-{recruiter_id}")
        if session.status == "ready":
            session.status = "in_progress"
            session.started_at = datetime.now(timezone.utc)
            session.version += 1
        await db.flush()
        details = session_details(session)
        await db.commit()
        return {**details, **token}


async def recruiter_status(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        if session is None:
            return {"status": "not_started", "candidate_admitted": False}
        invitation = await db.scalar(select(InterviewInvitation).where(
            InterviewInvitation.application_id == application_id,
            InterviewInvitation.job_description_id == job_id,
            InterviewInvitation.recruiter_id == recruiter_id,
        ))
        admitted = bool(
            session.status == "in_progress" and invitation
            and invitation.revoked_at is None
            and invitation.expires_at > datetime.now(timezone.utc)
            and session.candidate_admitted_at
            and session.admitted_invitation_hash == invitation.token_hash
        )
        return {**session_details(session), "candidate_admitted": admitted}


async def admit_candidate(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        require_active(session)
        invitation = await active_invitation(db, job_id, application_id, recruiter_id)
        if session.admitted_invitation_hash != invitation.token_hash:
            session.candidate_admitted_at = datetime.now(timezone.utc)
            session.admitted_invitation_hash = invitation.token_hash
            session.version += 1
        details = session_details(session)
        await db.commit()
        return {**details, "candidate_admitted": True}


async def candidate_session(db, token):
    invitation = await valid_invitation(db, token)
    session = await owned_session(
        db, invitation.job_description_id,
        invitation.application_id, invitation.recruiter_id,
    )
    # Recheck after acquiring the job lock: expiry/reissue/revoke may have changed.
    invitation = await valid_invitation(db, token)
    return invitation, session


async def candidate_status(token):
    async with SessionFactory() as db:
        invitation, session = await candidate_session(db, token)
        if session is None or session.status == "ready":
            return {"status": "waiting_for_recruiter", "can_join": False}
        admitted = bool(
            session.status == "in_progress"
            and session.candidate_admitted_at
            and session.admitted_invitation_hash == invitation.token_hash
        )
        return {
            "status": session.status,
            "can_join": admitted,
            "waiting_for_admission": session.status == "in_progress" and not admitted,
        }


async def candidate_join(token):
    async with SessionFactory() as db:
        invitation, session = await candidate_session(db, token)
        require_active(session)
        if (
            session.candidate_admitted_at is None
            or session.admitted_invitation_hash != invitation.token_hash
        ):
            raise HTTPException(403, "Wait for the recruiter to admit you.")
        return create_join_token(session.room_name, f"candidate-{invitation.application_id}")


async def recruiter_join(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        require_active(session)
        return create_join_token(session.room_name, f"recruiter-{recruiter_id}")


async def end_interview(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        if session is None:
            raise HTTPException(404, "Interview session not found.")
        if session.status == "completed":
            return session_details(session)
        if session.status not in {"ready", "in_progress", "closing"}:
            raise HTTPException(409, "This interview cannot be ended.")
        if session.status != "closing":
            session.status = "closing"
            session.version += 1
        room_name = session.room_name
        await db.commit()

    try:
        await close_room(room_name)
    except (api.TwirpError, ClientError, TimeoutError):
        raise HTTPException(503, "Room closure is pending. Retry End interview.") from None

    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        if session is None:
            raise HTTPException(404, "Interview session not found.")
        if session.status == "closing":
            session.status = "completed"
            session.completed_at = datetime.now(timezone.utc)
            session.version += 1
        details = session_details(session)
        await db.commit()
        return details
```

Helpers are small and local: ownership/locking, active invitation lookup, response formatting and active-state validation. No generic workflow framework or background queue is added here. Holding the existing per-job lock during token signing serializes these actions with selection changes and invitation reissue/revoke. No network operation runs while that lock is held.

The first `candidate_session()` lookup can precede a revoke/reissue; the second populates fresh state after lock acquisition. Invalid links never disclose a room. Session details do not return room names or admission hashes. Candidate status polling is read-only and does not claim candidate presence; presence is added through browser/LiveKit events later.

## 5. Additions to interview_routes.py

Add this import:

```python
from app.services.interviews.sessions import (
    start_interview, recruiter_status, admit_candidate,
    candidate_status, candidate_join, recruiter_join, end_interview,
)
```

Append these routes, reusing the existing Recruiter dependency and InterviewEntryRequest with SecretStr:

```python
@router.post("/jobs/{job_id}/applications/{application_id}/session/start")
async def start_session(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await start_interview(job_id, application_id, recruiter.id)


@router.get("/jobs/{job_id}/applications/{application_id}/session")
async def get_session(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await recruiter_status(job_id, application_id, recruiter.id)


@router.post("/jobs/{job_id}/applications/{application_id}/session/admit")
async def admit_session(job_id: UUID, application_id: UUID, recruiter: Recruiter):
    return await admit_candidate(job_id, application_id, recruiter.id)


@router.post("/jobs/{job_id}/applications/{application_id}/session/join")
async def join_host(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await recruiter_join(job_id, application_id, recruiter.id)


@router.post("/jobs/{job_id}/applications/{application_id}/session/end")
async def end_session(job_id: UUID, application_id: UUID, recruiter: Recruiter):
    return await end_interview(job_id, application_id, recruiter.id)


@router.post("/candidate/status")
async def get_candidate_status(request: InterviewEntryRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await candidate_status(request.token.get_secret_value())


@router.post("/candidate/join")
async def join_candidate(request: InterviewEntryRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await candidate_join(request.token.get_secret_value())
```

All candidate link tokens stay in POST bodies and are represented as SecretStr in request validation. Host identity comes from authentication, never from a request field. Session start/end are repeatable; reconnect uses join rather than creating another session. Authorization remains application-scoped.

## Verification required after approval

1. Offline migration SQL and live schema checks; user runs Docker/migration commands.
2. PostgreSQL fixtures: foreign-job/recruiter rejection, missing shortlist/invitation, repeated/concurrent starts, no admission no candidate token, expired/revoked/reissued invitation, completed/closing token rejection, admission/end race and retry after room-closure failure. Remove test-owned fixtures only.
3. API requests: staff auth/role checks, invalid UUID/token requests, no-store token responses and safe error text. Provider deletion is mocked for focused tests; that is not a live-room test.
4. User-run live room checks with the dev LiveKit server, then UI and actual media tests.

## Self-hosted limitation and remaining safeguards

LiveKit documents that self-hosted removal/permission changes do not invalidate previously issued tokens. The API above blocks new token issuance after closure, and closes the current room, but a cached valid token can still be used directly against self-hosted LiveKit. SDK token refresh also means a five-minute app token is not a universal five-minute replay bound. Do not promise immediate permanent exclusion based on room deletion alone. [Official token lifecycle documentation](https://docs.livekit.io/frontends/reference/tokens-grants/).

This is a local application-flow checkpoint. For remote/production admission/end guarantees, use Cloud revocation with explicit participant removal/revocation cutoffs, or add tested self-hosted admission/reconciliation enforcement. Current devkey/secret are public development credentials and are only suitable for local tests. Active invitation revocation/reissue must also disconnect existing candidate access; hash matching above only prevents future app-issued joins. Review those lifecycle hooks before remote release.

Duplicate tabs still use stable participant identities and can displace one another. Add an explicit attempt/takeover policy with the UI; do not label it protected by this proposal. Automatic recovery of closing sessions after process failure requires reconciliation; this proposal provides manual retry only. Rate limits, webhook verification, recording and presence remain pending separately.
