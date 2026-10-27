# Distill AI Backend

The Python/FastAPI backend provides Distill AI's recruitment API, authenticated AI tools, evidence-processing workflows and durable workers. It owns authentication, resource authorization, deterministic scoring, source/version integrity, interview admission and persistence. Use the [project README](../README.md) for positioning, the complete feature inventory and full-stack setup.

## Architecture and responsibilities

Requests pass through authentication and owned job/application checks into feature services and LangGraph workflows. PostgreSQL stores relational records, queues, versions and checkpoints; ChromaDB supplies scoped evidence retrieval; mounted storage retains CV versions, synthetic documents and face-model assets. Long-running workers process imports, screening, invitations, assessments and F11 tasks independently of the browser.

FastAPI/Pydantic define validated contracts, SQLAlchemy/Alembic maintain durable relational state, and LangGraph exposes explicit stages/recovery. Gemini/Ollama adapters separate model transport from feature rules. LiveKit media, Deepgram speech and SMTP delivery have separate integration boundaries.

```text
app/
  main.py                 FastAPI setup, lifecycle, and health endpoints
  *_routes.py             CV, interview, identity, assessment and resource endpoints
  auth.py                 Authentication endpoints and access dependencies
  jd.py                   Job description HTTP endpoints
  assistant.py            Recruiter assistant HTTP endpoints
  dashboard.py            Recruitment summaries from owned saved records
  mcp_cv.py               Authenticated CV MCP tools
  mcp_recruitment.py      Authenticated assistant recruitment tools
  mcp_email.py            Separate message-bound email MCP service
  core/                   Configuration, security, logging, and errors
  db/                     SQLAlchemy session and model registration
  models/                 SQLAlchemy database models
  services/
    assistant/            Recruiter assistant conversation operations
    cv/                   Intake, parsing, indexing, scoring and application deletion
    jd/                   JD conversations, references, profiles, and job posts
    documents/            Shared document extraction
    interviews/           Sessions, transcripts, identification and assessments
    wind_tunnel/          Synthetic rehearsal, evaluation and reviewed suggestions
  integrations/           Google, models, Discord, calendar, SMTP and MCP clients
  workflows/              LangGraph graphs, nodes, state, and tools
  workers/                Durable feature workers and extraction subprocess
  scripts/                Account/model setup and diagnostic/sample utilities
migrations/               Alembic database migration history
tests/                    Automated tests and synthetic verification tools
```

## Where code belongs

- HTTP input and authentication checks belong in route modules.
- Feature operations belong in the corresponding `services` package.
- Shared file extraction belongs in `services.documents`.
- External API transport belongs in `integrations`. Feature-specific prompt and
  validation logic stays with its feature, for example `services.cv.llm`.
- Graph orchestration belongs in `workflows`; feature worker entry points are in `app.workers`, not the older `app.scripts` paths.
- The OCR/native extraction subprocess is `app.workers.cv_extract`. It is
  launched internally by `services.cv.text`, rather than started manually.
- Database schema changes belong in `models` and Alembic migrations.

Keep package initializers lightweight. Import functions from their defining
modules, for example `from app.services.cv.scoring import calculate_scores`.
Services may reuse shared document helpers and integrations. Existing shared
embedding support remains in `services.jd.reference_index` and is also used by
CV indexing.

| Compose service | Entry point | Responsibility |
| --- | --- | --- |
| `backend` | `uvicorn app.main:app` | API, mounted CV/recruitment MCP and interview reconciliation. |
| `cv-worker` | `app.workers.cv_screening_worker` | Queued parsing, matching and scoring. |
| `cv-source-check-worker` | `app.workers.cv_source_check_worker` | Eligible source imports every minute, identification expiry and pending deletion cleanup. |
| `assessment-worker` | `app.workers.assessment_worker` | F5/F6 and post-interview analysis. |
| `wind-tunnel-worker` | `app.workers.wind_tunnel_worker` | F11 tasks with leases, saved progress and recovery. |
| `email-mcp` | `uvicorn app.mcp_email:app --port 8003` | Authenticated SMTP tool. |
| `invitation-worker` | `app.workers.invitation_worker` | Confirmed invitation queue; interrupted sends become uncertain. |

## Setup and configuration

### Docker

Follow [root setup](../README.md#setup) for credentials, image builds, infrastructure, embeddings and local startup. From the repository root, after PostgreSQL is ready:

```powershell
docker compose run --rm --no-deps backend python -m alembic upgrade head
docker compose exec backend python -m alembic current
```

Migrations are explicit; API startup does not apply schema upgrades. Keep the existing database rather than resetting it to migrate. Face models are installed through `app.scripts.setup_face_models`; the normal model mount is read-only, so use the temporary writable override in the root guide.

### Host Python development

With Python 3.12 and the required infrastructure available, run from `backend`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Before migration/startup, configure the repository-root `.env` for host execution and install Tesseract, its configured language data and Poppler on PATH. Python packages do not install the native extraction tools. Do not run host Uvicorn and the Compose backend on the same port.

| Host setting | Requirement |
| --- | --- |
| PostgreSQL | `POSTGRES_HOST=127.0.0.1`, `POSTGRES_PORT=5442` for the supplied Compose database. |
| Models/vector service | `OLLAMA_BASE_URL=http://127.0.0.1:11435`, `CHROMA_HOST=127.0.0.1`, `CHROMA_PORT=8001`. |
| Storage | Explicit writable `CV_STORAGE_PATH` and `WIND_TUNNEL_STORAGE_PATH`; `FACE_MODEL_PATH` points to installed assets. Populate/remove blank path entries from the example. |
| Google | `GOOGLE_APPLICATION_CREDENTIALS` is the real host JSON path. `GOOGLE_DRIVE_CREDENTIALS_HOST_PATH` is the Compose bind source, not the Python setting. |
| MCP | Use host-accessible tool URLs; set `RECRUITMENT_MCP_URL=http://127.0.0.1:8000/mcp/recruitment/` rather than the Docker-only default hostname. |
| Local media | `LIVEKIT_API_URL=http://127.0.0.1:7880`, browser `LIVEKIT_WS_URL=ws://localhost:7880` and local-development credentials. |

Start required host workers in separate terminals in the same configured environment, for example `python -m app.workers.cv_screening_worker`. Starting only the API does not consume all durable queues. Choose one coherent host/container topology rather than mixing incompatible endpoints.

### Model settings

- Chat uses `GEMINI_API_KEY` / `GEMINI_CHAT_MODEL`.
- CV parsing uses `CV_MODEL_PROVIDER`, `CV_PARSER_MODEL` or `GEMINI_CV_MODEL`. `auto` selects Gemini when a key exists; retrieval still needs the configured Ollama embedding model.
- F5, F6 and post-interview models use independent `F5_MODEL_*`, `F6_MODEL_*`, `POST_INTERVIEW_MODEL_*` settings. Local CV inference does not automatically make those workflows local.
- Read [config.py](app/core/config.py), [.env.example](../.env.example) and [compose.yaml](../compose.yaml) together. Compose overrides several endpoints, storage roots and media settings.
- Settings are cached per process. Restart host processes or recreate containers after environment changes. Preserve the JWT secret across routine restarts because it also protects existing invitation queue payloads.

## API and processing usage

The [OpenAPI interface](http://localhost:8000/docs) defines exact current payloads. Direct backend paths omit the frontend's `/api` proxy prefix.

| Route family | Purpose |
| --- | --- |
| `/auth` | Signup, login, refresh, logout and identity. |
| `/jd`, `/assistant` | Conversations, drafting, saved jobs, reviewed changes and resource lookup. |
| `/jd/references`, `/question-banks` | Supporting reference and question-bank resources. |
| `/cv` | Sources, applications, parsing, screening and confirmed application deletion. |
| `/interviews` | Shortlists, schedules, invitation review/delivery/status, sessions, transcripts and identity. |
| `/assessments` | F5/F6/post-interview runs, scoring settings, reports and final selection. |
| `/jd/descriptions/{job_id}/suggestions` | F11 batches, preview and reviewed Apply. |
| `/dashboard` | Saved-state recruitment metrics/activity. |
| `/mcp/`, `/mcp/recruitment/` | Authenticated CV/recruitment tools; email MCP runs separately. |

Normal browser access uses bearer tokens tied to revocable sessions. Candidate entry uses a private invitation token in the request body and grants no recruiter privileges. Long-running starts expose saved task/run state for polling, not a guaranteed completion time.

The usual sequence is approved job/profile, connected Sheet/Drive intake, screening, saved shortlist, scheduled/confirmed invitation, admission/interview, finalized transcript, reviewed F5, F6 and optional F8-F10 analyses, report/comparison and explicit final selection. F11 rehearses the saved configuration separately. Application deletion is an owned, explicitly confirmed operation for one job.

Fixed response headers are Name, Email, CV File and Image; Timestamp is optional. Every valid row needs CV links and exactly one reference-image link. Source/profile/model versions are pinned; changes can make prior results outdated. Interview completion, transcript readiness, analysis completion and final-selection eligibility are distinct states. The default final score uses 30% CV and 70% F9 with lineage/coverage checks, not a second average of F5 scores.

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

Container examples from the repository root:

```powershell
docker compose exec backend python -m unittest discover -s tests -p test_interview_invitations.py
docker compose exec backend python -m unittest discover -s tests -p test_email_mcp.py
docker compose exec backend python -m unittest discover -s tests -p test_assistant_scope.py
```

Tests are copied into the backend image, not source-mounted; rebuild/recreate it after editing tests. Read module-specific opt-ins such as `RUN_AUTH_DB_TESTS`, `RUN_CV_IMPORT_DB_TESTS`, `RUN_DELETION_DB_TESTS` and live-model flags before enabling them. Database checks write synthetic fixtures/isolated schemas; model checks may transmit text and consume provider quota. Some sample helpers expect `docs/fixtures/interview-analysis/jd-5`, which is absent from the reorganized docs folder. Sample availability is not a reason to substitute personal data.

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

## Privacy and security boundaries

Shared services enforce recruiter/job/application ownership. MCP uses authenticated scoped capabilities, with message-bound short-lived credentials for email. Candidate documents and retrieved text are untrusted data; schema/evidence validation supplements model instructions. Reviewed invitations, suggestions, final selections and deletions preserve human control.

Gemini can receive job/CV/transcript/proposal text; Deepgram receives speech, LiveKit transports media, SMTP receives private invitation content, and Google retains source originals. Configuring local CV inference changes that workflow only. See [root privacy notes](../README.md#privacy-and-external-services) for all external data paths.

F7 comparison bytes/embeddings are processed in memory; consent/result metadata has retention and withdrawal handling. Similarity is not identity confidence, liveness or proof. Application deletion preserves other jobs, chat history, external copies and backups. Cleanup retries recorded CV files, vectors and parsing checkpoints through the source worker; complete screening-checkpoint erasure is not established.

Passwords/session secrets are hashed and invitation queue payloads are encrypted; encryption of all candidate data/backups is not established. Keep secrets, CVs, transcripts, photos and dumps outside commits. Development environment settings, shared worker credentials and local media defaults do not establish production hardening.

## Operations and known limitations

```powershell
docker compose logs --tail 100 backend cv-worker cv-source-check-worker assessment-worker
docker compose exec backend python -m alembic current
docker compose exec ollama ollama list
```

Backend source reloads in development; workers do not automatically reload. Restart them when active work permits. Rebuild images for dependency changes and recreate containers for environment changes. Backups need database, vector and document state together.

Missing provider configuration, quota/context limits, unavailable embeddings, stale versions and unfinalized transcripts produce explicit blockers. Retry after resolving the cause. OCR, speech and semantic judgments still require representative validation; queues and hashes do not prove hiring validity.

The stack lacks MFA, login/signup throttling, full candidate-wide erasure, automatic offers/selection emails and a dedicated F10 cross-candidate process-consistency workflow. F11 has two synthetic personas and one assessment each, not statistical validation. Local HTTP/WS, development credentials, source mounts and floating image tags require a separate production deployment review.
