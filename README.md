# Distill AI

**AI-powered recruitment and interview intelligence for evidence-based human review.**

Distill AI helps recruiters turn job requirements, CVs and interview evidence into traceable candidate assessments in one recruitment workspace.

Create and review job descriptions, import applications from Google Sheets and Drive, screen CVs against saved criteria, schedule interviews, and inspect the evidence behind assessment results.

## Key features

- **Job descriptions and recruiter chat:** draft, revise and approve job descriptions with structured screening criteria; look up saved jobs and screening results directly in chat.
- **Evidence-based CV screening:** import connected applications every minute, extract PDF/DOCX content with OCR support, and track criterion matching, weighted scores and tied rankings.
- **Scheduled interviews:** review personalized invitations, attach calendar files, manage joining windows, and host LiveKit interviews with Deepgram transcription.
- **Traceable assessments:** inspect Q&A analysis, consolidated CV/interview findings, claim reviews, performance dimensions and interview quality using pinned source evidence.
- **Reviewed AI suggestions:** rehearse screening with synthetic CVs, review proposed job-description and criteria changes together, and explicitly confirm the selected changes before applying them.
- **Candidate decisions:** compare two saved assessments, review versioned interview scoring settings, export reports and explicitly confirm final candidate selections. Saving a final selection sends no email or offer.
- **Application deletion:** open Delete candidate in CV Screening, review its scope and confirm removal for the selected job; cleanup retries and import exclusions prevent silent restoration of the original CV uploads.

Results support recruiter review. Evidence gaps, outdated results and processing failures are represented explicitly; scores are not hiring decisions.

| Feature | Implemented scope |
| --- | --- |
| F1 / F2 | PDF/DOCX CV extraction, OCR, semantic criterion matching, weighted dimension scores and tied ranks. |
| F3 / F4 | Recruiter-led audio/video interviews, joining windows and admission, speaker-attributed transcription, saved turns and finalization. |
| F5 / F6 | Q&A grouping/mapping; relevance, accuracy, completeness and articulation; consolidated CV/interview findings, evidence navigation, comparison and reports. |
| F7 | Consent-based one-to-one photo comparison, one-face countdown, bounded retakes and recruiter-only similarity/manual review. |
| F8 / F9 | CV claim review and append-only recruiter feedback; versioned performance dimensions and evidence-gated combined scores. |
| F10 | Single-interview question quality, follow-up, competency coverage and measured speaking time when reliable timing exists. |
| F11 Wind Tunnel | Isolated synthetic screening rehearsal and grounded job/criteria suggestions that require reviewed Apply. Current runs use two personas and one assessment per persona. |

Supporting features include recruiter accounts/sessions, dashboard and chat, reference documents, question banks, manual JD editing, explicit Discord posting, connected application intake, shortlists, invitations and durable workers.

The default combined score is **30% saved CV score + 70% saved F9 interview performance**. Both components and compatible artifacts are required. Coverage must satisfy the configured policy and the combined-score gates, including 80% assessed interview weight, four dimensions and technical competency. F5 Q&A averages are not a substitute for F9 performance.

## Tech stack and architecture

| Layer | Technologies |
| --- | --- |
| Frontend | TypeScript, React 19, Vite 8, React Router, Tailwind CSS 4 |
| Backend | Python 3.12, FastAPI, Pydantic, SQLAlchemy, Alembic |
| Persistence | PostgreSQL 16, PostgreSQL-backed LangGraph checkpoints, local document storage |
| AI workflows | LangGraph, LangChain, Google Gemini, Ollama, authenticated MCP tools |
| Retrieval and documents | ChromaDB, Qwen3 embeddings, Tesseract OCR, Poppler, pypdf, python-docx |
| Interviews | LiveKit, Deepgram, OpenCV |
| Integrations | Google Drive, Google Sheets, Gmail SMTP, Discord webhooks |
| Development | Docker Compose, ESLint, Node.js test runner, Python unittest |

The browser calls FastAPI through the frontend's `/api` proxy. Routes delegate to services and LangGraph workflows. Separate workers handle imports, screening, synthetic screening rehearsals, assessments and invitation delivery.

React/TypeScript support a responsive, typed workspace; FastAPI/Pydantic define validated API contracts. PostgreSQL and Alembic preserve relational ownership and explicit schema evolution. LangGraph makes multi-stage work and recovery explicit; scoped MCP credentials constrain tool capabilities. ChromaDB supplies evidence retrieval, and local extraction/OCR avoids an external OCR API. Dedicated LiveKit and Deepgram integrations handle media and speech separately from assessment logic.

```text
React workspace
    |
    v
FastAPI --> Services / LangGraph workflows --> Authenticated MCP tools
                 |
                 +--> PostgreSQL: records, queues and checkpoints
                 +--> ChromaDB: retrievable evidence
                 +--> Mounted storage: CVs and synthetic documents
                 +--> Gemini / Ollama: inference and embeddings
                 +--> Drive / Sheets / SMTP / LiveKit / Deepgram

Background workers --> Durable processing and saved results
```

## Setup

### Prerequisites

- Git and Docker with Docker Compose v2; Docker Desktop supports Windows development.
- A Gemini API key for recruiter chat and the default interview-analysis configuration.
- A Google service-account JSON file with access to the connected Drive folders and response Sheets. The supplied Compose configuration requires an existing credentials file to mount.
- Available memory and disk space for the multi-service stack and configured Ollama models.
- For running tools outside Docker: Python 3.12, Node.js 24 and npm. Docker images install application dependencies, Tesseract and Poppler.

The following commands use PowerShell from the repository root. The supplied Compose stack runs development servers with source mounts and reload enabled.

### 1. Clone and configure

```powershell
git clone https://github.com/KaziRafiulKader09/Distill_AI.git
Set-Location Distill_AI
git switch dev
Copy-Item .env.example .env
```

`dev` is the integrated feature branch used by this guide. Use the intended release branch when working from a separately published release.

Copy the environment file only for a new checkout; preserve an existing `.env`. Configure it before starting services:

| Variables | Purpose |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials; replace the example password. |
| `JWT_SECRET_KEY` | Private signing secret of at least 32 characters. |
| `GEMINI_API_KEY`, `GEMINI_CHAT_MODEL` | Recruiter chat credentials and model. |
| `GOOGLE_DRIVE_CREDENTIALS_HOST_PATH` | Absolute host path to an existing service-account JSON file; required by Compose. |
| `GOOGLE_DRIVE_CV_ROOT_FOLDER_ID` | Drive root used by CV intake. |
| `CV_MODEL_PROVIDER`, `CV_PARSER_MODEL`, `GEMINI_CV_MODEL` | CV inference settings. `auto` selects Gemini when a key is configured; `ollama` selects local CV inference. |
| `F5_MODEL_*`, `F6_MODEL_*`, `POST_INTERVIEW_MODEL_*` | Independent Q&A, consolidation and post-interview settings; defaults use Gemini. Selecting local CV inference does not make chat or all assessments local. |
| `PUBLIC_APP_URL` | Browser-facing origin; defaults to `http://localhost:5173`. |
| `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_MCP_SECRET` | Invitation delivery credentials. Use a Gmail app password and a separate MCP secret of at least 32 characters. |
| `DEEPGRAM_API_KEY` | Required when starting live transcription. |
| `LIVEKIT_API_URL`, `LIVEKIT_WS_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Interview media configuration; Compose provides same-PC development defaults. |
| `DISCORD_WEBHOOK_URL` | Optional approved-job posting integration. |
| `OLLAMA_BASE_URL`, `OLLAMA_EMBEDDING_MODEL`, `CHROMA_HOST`, `CHROMA_PORT` | Model/vector destinations; Compose supplies internal addresses and the embedding model. |
| `CV_STORAGE_PATH`, `WIND_TUNNEL_STORAGE_PATH`, `FACE_MODEL_PATH` | Document/model roots; Compose uses mounted paths. Populate or remove blank path entries for host execution. |
| `ACCESS_TOKEN_EXPIRE_MINUTES`, `AUTH_SESSION_DAYS` | Default access-token/session lifetimes: 30 minutes/seven days. |
| `GEMINI_REQUESTS_PER_MINUTE`, `GEMINI_MAX_RETRIES` | Shared model scheduling, capped at 12 RPM; this is not login-abuse protection. |
| `CV_MCP_URL`, `RECRUITMENT_MCP_URL`, `EMAIL_MCP_URL` | Authenticated internal tool URLs; host and Docker destinations differ. |
| `INTERVIEW_LINK_DAYS`, `INTERVIEW_EMPTY_GRACE_SECONDS` | Defaults: seven-day invitation lifetime and 60-second empty-room grace. |
| `FACE_MATCH_THRESHOLD`, `IDENTITY_RETENTION_DAYS` | Optional calibrated similarity threshold and default 30-day enrollment retention. |

See [the environment example](.env.example) and [backend settings](backend/app/core/config.py) for supported options. Compose overrides database, Ollama, ChromaDB and internal MCP addresses for container networking.

Use an existing service-account file outside the repository, with a host path such as `C:/private/distill-service-account.json`. Compose requires that bind source even when exploring only the UI. Replace the example database password. Recreate containers after environment changes; process settings are cached. Preserve `JWT_SECRET_KEY` across routine restarts because it also protects existing encrypted invitation payloads; plan key rotation explicitly.

### 2. Install dependencies and initialize storage

```powershell
docker compose build backend frontend
```

The image builds install Python dependencies from `backend/requirements.txt` and frontend dependencies using `npm ci`.

After configuring the credentials-file mount, generate a secret locally:

```powershell
docker compose run --rm --no-deps backend python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy it to `JWT_SECRET_KEY` in your private environment file before application startup; the example value is shorter than the required minimum. Generate a separate value for `EMAIL_MCP_SECRET` when enabling invitation delivery. The generator uses Python's standard library without starting the API. Keep environment files and service-account credentials out of commits.

Start infrastructure, download the embedding model and apply database migrations:

```powershell
docker compose config --quiet
docker compose up -d --wait postgres ollama chromadb
docker compose exec ollama ollama pull qwen3-embedding:0.6b
docker compose run --rm --no-deps backend python -m alembic upgrade head
```

Ollama embeddings are used even when CV inference uses Gemini. If you select local CV inference, also download the configured parser model:

```powershell
docker compose exec ollama ollama pull qwen3:4b
```

### 3. Run the application

```powershell
$appServices = @(
    'backend', 'frontend', 'livekit', 'email-mcp',
    'cv-worker', 'cv-source-check-worker', 'invitation-worker',
    'assessment-worker', 'wind-tunnel-worker'
)
docker compose up -d --build $appServices
docker compose ps
```

The explicit list starts local application services and their dependencies. Plain `docker compose up -d` also starts the configured **Cloudflare tunnel**, exposing the frontend and proxied API publicly. Start it only when public testing is intended.

| Service | Local address |
| --- | --- |
| Recruitment workspace | [localhost:5173](http://localhost:5173) |
| Interactive API documentation | [localhost:8000/docs](http://localhost:8000/docs) |
| API health | [localhost:8000/health](http://localhost:8000/health) |
| Database health | [localhost:8000/health/db](http://localhost:8000/health/db) |
| PostgreSQL / ChromaDB / Ollama | `127.0.0.1:5442` / `127.0.0.1:8001` / `127.0.0.1:11435` |
| Local LiveKit | `ws://localhost:7880` with loopback TCP/UDP media ports |
| Email MCP | `http://localhost:8003/mcp/` - authenticated worker interface |

Choose **Create an account** on the sign-in page to register a recruiter. Passwords require at least 12 characters. You can also create a staff account interactively:

```powershell
$staffEmail = Read-Host "Staff email"
docker compose exec backend python -m app.scripts.create_user --email $staffEmail --role recruiter
```

The script prompts for a password and confirmation. It also accepts `--role interviewer`, but the delivered recruitment interface and management routes require a recruiter. No separate interviewer/reviewer workspace is implemented.

### Local frontend development

To run the frontend on the host with the Docker backend, stop the Compose frontend first to free port 5173:

```powershell
docker compose stop frontend
Set-Location frontend
npm ci
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8000` by default. Run `npm run build` to generate the frontend bundle.

### Interview setup

The bundled LiveKit service supports same-PC development. Remote interviews require a reachable LiveKit deployment or LiveKit Cloud, matching API/browser addresses, and an HTTPS application origin for browser media access.

Live transcription requires Deepgram configuration. Candidate photo comparison requires checksum-verified YuNet/SFace models and their licenses. The normal face-model mount is read-only; install with a temporary writable override:

```powershell
New-Item -ItemType Directory -Force storage/face_models | Out-Null
docker compose run --rm --no-deps --volume "${PWD}/storage/face_models:/workspace/storage/face_models" backend python -m app.scripts.setup_face_models
docker compose restart backend
```

Restart only when existing interview work permits. Without `FACE_MATCH_THRESHOLD`, similarity remains available without an automatic match verdict. A benchmark example threshold is not local operational calibration.

For temporary public testing, start `cloudflared`, inspect its URL, set `PUBLIC_APP_URL` to the HTTPS origin and `VITE_ALLOWED_HOST` to its hostname only, then recreate affected services. The tunnel carries application traffic, not LiveKit media transport; remote media still needs reachable LiveKit addresses. Previously delivered invitation URLs do not update automatically.

Optional pgAdmin is available through `docker compose --profile tools up -d pgadmin` at [localhost:5050](http://localhost:5050); configure its login variables separately.

## Usage

1. Draft a job description with the assistant, review its criteria and weights, and approve it.
2. Create the Form/response Sheet and upload destinations externally. Share the Sheet, CV folder and reference images with the service account. Connect the job to a direct child CV folder of the configured parent and the response Sheet tab.
3. Select **Screen applications** to parse CVs, match criteria and calculate scores. Review evidence and processing issues before saving a shortlist.
4. Set interview times, preview recipient-specific invitations and confirm delivery.
5. Conduct the interview and inspect saved Q&A analysis, consolidated findings and post-interview assessments.
6. Review supported AI suggestions in the job workspace and confirm the exact selected changes before applying them.
7. Compare saved assessments, inspect report evidence and explicitly save final candidates when interview, source and scoring eligibility requirements are satisfied.
8. To remove an application, choose **Delete candidate** in CV Screening, read the retained-data boundary and confirm. Active processing can block deletion. Pending storage cleanup is reported and retried by the source-check worker; rerun screening for current remaining rankings.

Response headers must contain exactly one each of **Name**, **Email**, **CV File** and **Image**. Header ordering is irrelevant; case/outer spaces are normalized. `Timestamp` is optional; extra columns are ignored. A valid row needs supported CV Drive links and exactly one reference-image link, even though camera comparison can be declined. Eligible sources are checked every minute, independently of an open browser; provider work/backoff can extend the interval.

New/reissued scheduled invitations include calendar artifacts; failed-send retries preserve their reviewed payload. Joining opens ten minutes before the scheduled start and closes at the end. Candidate entry includes devices, consent and waiting for recruiter admission. Low similarity does not automatically reject a person. Interview completion and transcript readiness remain separate stages.

## Privacy and external services

| Service | Data involved |
| --- | --- |
| Google Sheets / Drive | Application contact/source metadata, CV files and reference photos read through the service account. Form creation and original retention remain external responsibilities. |
| Gemini, when selected | Job text, recruiter prompts/history, CV/profile/evidence text, transcript excerpts and synthetic/proposal context required by the selected workflow. |
| Ollama / ChromaDB | Inference/embeddings and retrievable evidence text/metadata in the configured deployment. A remote endpoint changes the data boundary. |
| LiveKit / Deepgram | Participant audio/video and media metadata / speech audio and transcription hints. |
| Gmail SMTP | Recipient, personalized body, private joining link and calendar attachment. |
| Discord, when requested | Approved job-post content. |
| Cloudflare, when started | Public frontend and proxied API traffic; separate from LiveKit media. |

Use synthetic or appropriately consented data. Keep environment files, service-account JSON, personal CVs, transcripts, photos, screenshots and dumps out of commits. Ignore rules reduce accidents; they do not prove a clean repository history or vulnerability-free dependencies.

PostgreSQL retains business records, transcripts, assessment artifacts and workflow checkpoints. ChromaDB retains text/embedding evidence; mounted storage retains CVs and synthetic documents. Face comparison processes reference bytes, frames and embeddings in memory; application consent/result metadata is retained separately. Identification supports withdrawal/expiry, with a default 30-day initial enrollment expiry that reimport does not extend. The original photo stays in Drive. Similarity is resemblance, not identity confidence or liveness proof.

Application deletion is **not candidate-wide erasure**. It preserves other jobs, existing chat history, Google originals/rows, delivered emails, downloaded reports and backups. Cleanup covers recorded CV files, vector indexes and parsing checkpoint threads; complete screening-checkpoint erasure is not established. Logical removal may precede external cleanup. New uploads with new Drive IDs may be imported as new submissions.

Passwords and browser-session secrets are hashed; invitation queue payloads are encrypted. This does not establish encryption of all CV/transcript/database/backup data at rest. Production TLS, processor arrangements, retention, key rotation and backup restoration require an explicit operational policy.

### API example: read saved job descriptions

Authenticate with an existing recruiter account and retrieve its saved jobs:

```powershell
$credential = Get-Credential -Message "Enter your Distill AI recruiter credentials"
$loginBody = @{
    email = $credential.UserName
    password = $credential.GetNetworkCredential().Password
} | ConvertTo-Json

$session = Invoke-RestMethod -Method Post `
    -Uri "http://localhost:8000/auth/login" `
    -ContentType "application/json" -Body $loginBody

$headers = @{ Authorization = "Bearer $($session.access_token)" }
Invoke-RestMethod -Uri "http://localhost:8000/jd/descriptions" -Headers $headers
```

This reads saved recruiter-owned descriptions without starting screening or a model workflow. Explore other request and response schemas in the [interactive API documentation](http://localhost:8000/docs).

## Development and verification

Run frontend unit checks, lint and a bundle build from the repository root:

```powershell
docker compose exec frontend npm test
docker compose exec frontend npm run lint
docker compose exec frontend npm run build
```

The backend uses `unittest`. Focused examples without live model calls:

```powershell
docker compose exec backend python -m unittest discover -s tests -p test_cv_screening_progress.py
docker compose exec backend python -m unittest discover -s tests -p test_invitation_scheduling.py
```

Database and live-model checks use opt-in settings defined in their test modules and may create synthetic fixtures or consume provider quota. Configure the required services before enabling them. Backend tests are copied into the image, not source-mounted: rebuild/recreate the backend after changing tests before relying on container results. Browser smoke scripts require separate Playwright/browser tooling; see the [frontend guide](frontend/README.md#verification).

Inspect services and background processing:

```powershell
docker compose logs --tail 100 backend cv-worker cv-source-check-worker
Invoke-RestMethod http://localhost:8000/health/db
```

Expected database health response:

```json
{"status":"ok","database":"connected"}
```

Source mounts update files visible to workers, but worker processes do not automatically reload. Restart the affected worker after relevant changes when its active work permits. Use `docker compose stop` to stop services while retaining persisted data.

Use `docker compose down` without `--volumes` when removing containers while retaining named volumes. A backup must cover PostgreSQL, mounted documents and vector data together; database backup alone is insufficient.

## Known limitations

- Compose uses development servers/source mounts, local LiveKit credentials and some floating image tags. It is not a hardened production topology; containers do not explicitly select non-root runtime users.
- Signup creates active recruiters. No MFA, email verification, password reset or application-level login throttling is implemented, and there is no independent reviewer permission model.
- OCR, model judgments and speech recognition require human review. English is the demonstrated baseline; no throughput, accuracy or production-latency guarantee is asserted.
- F11's two-persona, single-pass rehearsal does not measure repeatability or prove statistical confidence, fairness or improved outcomes after edits. Useful suggestions are not guaranteed.
- F10 evaluates individual interview process quality. A dedicated cross-candidate process-consistency workflow is not implemented; F6 candidate comparison serves a different purpose.
- Unreliable/text-only transcript timing cannot provide measured speaking-time totals. Missing/stale artifacts can block downstream scores and final selection. Local media checks do not establish remote two-device readiness.
- Application deletion has the boundaries above. Saving final candidates does not send selection notifications or offers.
- Some synthetic analysis helpers expect `docs/fixtures/interview-analysis/jd-5`; that fixture set is absent from the current docs layout. Sample modes require suitable synthetic assets.

## Project documentation

- [Software Requirements Specification](docs/30224_Kazi_Rafiul_Kader_Distill_AI_SRS.docx)
- [Work breakdown structure](<docs/30224_Kazi_Rafiul_Kader_Distill AI_WBS.xlsx>)
- [System diagrams](docs/30224_Kazi_Rafiul_Kader_Distill_AI_Diagrams/)
- [LangGraph diagrams](docs/30224_Kazi_Rafiul_Kader_Distill_AI_LangGraphs/)
- [F11 innovation rationale](docs/30224_Kazi_Rafiul_Kader_Distill_AI_Innovation_Rationale.pdf)

## Repository layout

```text
backend/
  app/             Routes, services, integrations, workflows and workers
  migrations/      Alembic database migrations
  tests/           Unit, integration and acceptance checks
frontend/
  src/             Recruitment workspace and shared UI
  tests/           Unit tests and browser smoke scripts
docs/              SRS, WBS, system/LangGraph diagrams and innovation rationale
storage/           Local runtime artifacts; ignored by Git
compose.yaml       Development services and persistent volumes
```

See the [backend guide](backend/README.md) for services/workers and the [frontend guide](frontend/README.md) for client setup and verification.

## Contributing

Open an [issue](https://github.com/KaziRafiulKader09/Distill_AI/issues) to report a reproducible problem or discuss a feature. Pull requests should explain the behavior change, include relevant verification, and preserve source evidence, ownership checks and recovery behavior. Use synthetic candidate data in examples and tests.

## Maintainer

[KaziRafiulKader09](https://github.com/KaziRafiulKader09) · [GitHub repository](https://github.com/KaziRafiulKader09/Distill_AI)
