# Interview foundation — first code review

## Current requirement amendment — recruiter is the host

The user clarified that the recruiter who creates/owns the job conducts the interview. This supersedes the assigned-interviewer design in the historical proposal below. The user approved this amendment and migration application. The changes below are now implemented and live revision is **b4d2e9017c65**. Six scheduling validation tests, model/migration consistency, offline upgrade SQL and live schema inspection passed.

Approved and implemented simplification:

```diff
--- backend/app/models/interviews.py
+++ backend/app/models/interviews.py
@@ InterviewSession
-    interviewer_id: Mapped[UUID] = mapped_column(
-        ForeignKey("users.id", ondelete="RESTRICT"), index=True,
-    )

--- backend/app/services/interviews/schemas.py
+++ backend/app/services/interviews/schemas.py
@@ ScheduleInterviewRequest
-    interviewer_id: UUID

--- backend/migrations/versions/b4d2e9017c65_add_interview_sessions.py
+++ backend/migrations/versions/b4d2e9017c65_add_interview_sessions.py
@@ upgrade
-        sa.Column(
-            "interviewer_id", sa.Uuid(),
-            sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False,
-        ),
@@ indexes
-    for column in ("job_description_id", "recruiter_id", "interviewer_id"):
+    for column in ("job_description_id", "recruiter_id"):
```

Update the scheduling tests to create requests without interviewer_id and remove the obsolete invalid-interviewer-ID test. The future service derives recruiter_id from the authenticated user and verifies owned_job(); it must not accept a host ID from the client. The existing recruiter workspace provides host controls; there is no assignment API or separate interviewer screen. Transcript mapping still labels the recruiter participant as interviewer and the candidate as candidate.

The migration was confirmed unapplied locally before amendment, so it was revised directly and then applied after checks passed. The live table has no interviewer_id and uses recruiter_id as its host reference. No scheduling or joining API exists yet; the service will enforce actual job ownership. The class and SQL under the historical first-checkpoint section below show the superseded assigned-interviewer version.

## Historical approved first checkpoint

Prepared 13 October 2026 (Asia/Dhaka). The user approved this first checkpoint, and the model, schema and migration below are now implemented. Seven request-validation tests, model/migration consistency checks and offline upgrade/downgrade SQL checks passed. The live database revision check timed out; no migration has been applied. The proposal text below preserves what was reviewed. See DAILY_WORK_2026-10-13.md for execution details.

## Understanding and first step

The target is a human audio/video interview, initially tested locally. A recruiter schedules one selected candidate application and assigns a staff interviewer. The candidate uses their invitation; the interviewer signs in. Both ultimately join the same LiveKit room. Waiting/admission, reconnects and explicit completion will be implemented on top of a durable session.

Existing InterviewShortlistMember records identify selected applications. InterviewInvitation already belongs to an application and stores a hashed entry token. User supports recruiter/interviewer roles, while current interview routes require recruiter access. Reuse these records and authentication rather than create another candidate/account system.

First checkpoint: session storage and schedule input validation only. This is a reviewable foundation; it does not yet provide a callable scheduling or join endpoint. Next checkpoint adds a short service and thin API routes, using the existing SessionFactory and owned_job() conventions.

## Proposed model addition

Append this class to backend/app/models/interviews.py. Add CheckConstraint to the existing SQLAlchemy imports. Reuse the existing UUID, datetime, Base and mapped_column imports.

```python
class InterviewSession(Base):
    """One scheduled human interview per candidate application."""

    __tablename__ = "interview_sessions"
    __table_args__ = (
        CheckConstraint("ends_at > starts_at", name="ck_interview_session_times"),
        CheckConstraint(
            "status IN ('scheduled', 'in_progress', 'completed', 'cancelled')",
            name="ck_interview_session_status",
        ),
        CheckConstraint("version >= 1", name="ck_interview_session_version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        unique=True,
    )
    job_description_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True,
    )
    recruiter_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    interviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    display_timezone: Mapped[str] = mapped_column(String(100))
    room_name: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(
        String(24), default="scheduled", server_default="scheduled",
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )
```

The service generates room_name from an opaque UUID, never from candidate names/emails and never from client input. The room name reserves the interview's future LiveKit destination; storing it does not require LiveKit credentials or create a room.

The first MVP has one session per application, matching the current one-invitation-per-application design. Rescheduling edits a scheduled session and increments its version. Multiple interview rounds are a later explicit change. The version supports rejecting outdated updates; it is not an automatic lock by itself.

No invitation foreign key is necessary here: both records resolve through application_id. This allows scheduling before invitation generation and avoids coupling schedule persistence to email delivery. The service must still validate the full job/application/recruiter relationship; individual foreign keys do not establish that authorization.

Keep business statuses small. Connection and recording failures belong to their own operational state, and a disconnect must not become completion. Join leases, lifecycle operations and recording manifests will be added in the corresponding integration checkpoints; the single table above is not the entire reliability implementation.

## Proposed request addition

Append this schema to backend/app/services/interviews/schemas.py, with imports for timezone, ZoneInfo, ZoneInfoNotFoundError and AwareDatetime.

```python
class ScheduleInterviewRequest(BaseModel):
    interviewer_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    display_timezone: str = Field(default="Asia/Dhaka", max_length=100)

    @model_validator(mode="after")
    def validate_schedule(self):
        try:
            ZoneInfo(self.display_timezone)
        except ZoneInfoNotFoundError:
            raise ValueError("Use a valid timezone, such as Asia/Dhaka.") from None

        self.starts_at = self.starts_at.astimezone(timezone.utc)
        self.ends_at = self.ends_at.astimezone(timezone.utc)
        if self.ends_at <= self.starts_at:
            raise ValueError("Interview end must be after its start.")
        return self
```

Require timezone-aware input instead of guessing the timezone of an ambiguous timestamp. Store instants in UTC and the named timezone for display. Ensure IANA timezone data is available on Windows (tzdata dependency if not already supplied by the runtime). Duration limits and future-start validation belong in the scheduling service, where a shared current time can be used for consistent decisions/tests.

## Proposed migration and registration

Add a migration after repository revision a8b315c9e204. Its upgrade creates only interview_sessions with the columns, constraints, unique keys and indexes above; downgrade drops that table. Add InterviewSession to the imports in backend/migrations/env.py so Alembic sees it.

The proposed database shape is:

```sql
CREATE TABLE interview_sessions (
    id UUID PRIMARY KEY,
    application_id UUID NOT NULL UNIQUE
        REFERENCES candidate_applications(id) ON DELETE CASCADE,
    job_description_id UUID NOT NULL
        REFERENCES job_descriptions(id) ON DELETE CASCADE,
    recruiter_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    interviewer_id UUID NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    display_timezone VARCHAR(100) NOT NULL,
    room_name VARCHAR(100) NOT NULL UNIQUE,
    status VARCHAR(24) NOT NULL DEFAULT 'scheduled',
    version INTEGER NOT NULL DEFAULT 1,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_interview_session_times CHECK (ends_at > starts_at),
    CONSTRAINT ck_interview_session_status
        CHECK (status IN ('scheduled', 'in_progress', 'completed', 'cancelled')),
    CONSTRAINT ck_interview_session_version CHECK (version >= 1)
);
CREATE INDEX ix_interview_sessions_job_description_id
    ON interview_sessions (job_description_id);
CREATE INDEX ix_interview_sessions_recruiter_id
    ON interview_sessions (recruiter_id);
CREATE INDEX ix_interview_sessions_interviewer_id
    ON interview_sessions (interviewer_id);
```

Generate and inspect migration SQL first. Check the live revision before applying anything; older docs say the invitation queue migration has not been applied, and that historical statement has not been checked in this turn.

## Data flow in the next checkpoint

Recruiter request -> existing authentication -> owned_job() -> saved shortlist membership/application ownership check -> active interviewer validation -> schedule/overlap validation -> session write -> schedule response.

The job/application/recruiter/room/status values are backend-controlled. Serialize scheduling for an assigned interviewer before checking overlaps, so two concurrent requests cannot both book the same time. A database transaction protects writes; network calls stay outside it. For the owning recruiter to conduct the interview, select that user's ID explicitly. Other recruiter accounts cannot be assigned merely because they are staff. The current project has no organization/team model, so assignment is not a substitute for a future tenant/team boundary.

After that: candidate invitation validation -> session metadata -> waiting/admission -> server-issued LiveKit token -> room UI. Staff access checks assignment or owning-recruiter authority, rather than granting all interviewers access to all sessions.

## First-checkpoint verification

- Request validation accepts aware start/end and converts them to UTC; rejects naive timestamps, invalid timezone and end <= start.
- Model/migration metadata agrees; SQL has foreign keys, required fields, unique application/room constraints and named checks.
- Use focused schema tests and migration inspection. Ownership, overlap concurrency and lifecycle tests follow with the actual scheduling service; a model alone cannot verify them.

## Review agreement

DAILY_WORK_2026-10-12.md says: "Show the proposed file or edits for review, then wait for the recruiter/developer's explicit instruction to add them."

This file provides the proposed edits before implementation. Approval for this first checkpoint covers the model, schema, migration/registration and their focused validation; subsequent service/UI checkpoints will be explained before adding them.
