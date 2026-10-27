# Backend structure

The FastAPI backend keeps HTTP routes, application operations, workflow graphs,
database models, and external clients in separate packages.

```text
app/
  main.py                 FastAPI setup, lifecycle, and health endpoints
  *_routes.py             CV and JD reference HTTP endpoints
  auth.py                 Authentication endpoints and access dependencies
  jd.py                   Job description HTTP endpoints
  assistant.py            Recruiter assistant HTTP endpoints
  mcp_cv.py               Authenticated CV MCP tools
  core/                   Configuration, security, logging, and errors
  db/                     SQLAlchemy session and model registration
  models/                 SQLAlchemy database models
  services/
    assistant/            Recruiter assistant conversation operations
    cv/                   Ingestion, parsing, indexing, matching, and scoring
    jd/                   JD conversations, references, profiles, and job posts
    documents/            Shared document extraction
  integrations/           Google Drive, Google Sheets, Discord, CV model clients
  workflows/              LangGraph graphs, nodes, state, and tools
  workers/                Isolated document extraction subprocess
  scripts/                Account creation, screening worker, and MCP checks
migrations/               Alembic database migration history
tests/                    Automated tests and synthetic verification tools
```

## Where code belongs

- HTTP input and authentication checks belong in route modules.
- Feature operations belong in the corresponding `services` package.
- Shared file extraction belongs in `services.documents`.
- External API transport belongs in `integrations`. Feature-specific prompt and
  validation logic stays with its feature, for example `services.cv.llm`.
- Graph orchestration belongs in `workflows`; durable screening worker startup
  remains in `scripts.cv_screening_worker`.
- The OCR/native extraction subprocess is `app.workers.cv_extract`. It is
  launched internally by `services.cv.text`, rather than started manually.
- Database schema changes belong in `models` and Alembic migrations.

Keep package initializers lightweight. Import functions from their defining
modules, for example `from app.services.cv.scoring import calculate_scores`.
Services may reuse shared document helpers and integrations. Existing shared
embedding support remains in `services.jd.reference_index` and is also used by
CV indexing.

## Local checks

From `backend`, with the Python environment activated:

```powershell
python -m compileall -q app tests
python -m unittest discover -s tests -p test_cv_scoring.py
python -m unittest discover -s tests -p test_cv_gemini.py
python -m unittest discover -s tests -p test_jd_profiles.py
python -m unittest discover -s tests -p test_cv_parsing.py
```

The parsing suite includes document subprocess and OCR checks and needs the
system tools used by the backend image. PostgreSQL integration tests and manual
verification scripts require their configured services; run those against a
development database with synthetic data.

See the [root setup guide](../README.md) for application startup.

## Interview room cleanup

The API checks active interviews against LiveKit presence every 15 seconds.
After neither the recruiter nor candidate is present for 60 seconds, it ends
the interview, stops transcription, and deletes the room. Hidden transcription
participants do not count as people. Set `INTERVIEW_EMPTY_GRACE_SECONDS` (30–600)
to adjust the reconnect grace period. Cleanup runs while the backend is running;
failed presence checks do not count as an empty room.

End interview still closes the room immediately after flushing transcription.
If closure fails, its persisted `closing` state is retried automatically on
subsequent checks, including after a backend restart. Startup resumes transcription
only when a recruiter or candidate is actually connected. Existing abandoned
`in_progress` interviews are also cleaned up after the grace period.

## Recruiter chatbot scope

Central chat runs a structured, tool-free scope check on each new recruiter
message before routing actions. Entirely unrelated requests receive a fixed
Distill AI feature redirect. Invalid or unavailable scope decisions stop the
turn without executing an action. Tool-result explanations do not repeat the check.

The shared scope policy also guides central answers, interview intent, and JD
intent. It allows feature help, technical interview topics, greetings, mixed
requests' supported portions, and contextual follow-ups while rejecting attempts
to replace the assistant's role. Product features are distinguished from actions
the current chat's authenticated tools can actually perform.

Run `test_assistant_scope.py` for mocked guardrail regressions. Set
`RUN_ASSISTANT_SCOPE_MODEL_TESTS=1` to include eight real-model scope scenarios;
these use the configured model credentials and rate limiter.
