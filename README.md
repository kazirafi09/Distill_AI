# Distill AI

Distill AI helps recruiters turn job requirements, CVs and interview evidence into traceable candidate assessments in one recruitment workspace.

Create and review job descriptions, import applications from Google Sheets and Drive, screen CVs against saved criteria, schedule interviews, and inspect the evidence behind assessment results.

## Key features

- **Job descriptions and recruiter chat:** draft, revise and approve job descriptions with structured screening criteria; look up saved jobs and screening results directly in chat.
- **Evidence-based CV screening:** import connected applications every minute, extract PDF/DOCX content with OCR support, and track criterion matching, weighted scores and tied rankings.
- **Scheduled interviews:** review personalized invitations, attach calendar files, manage joining windows, and host LiveKit interviews with Deepgram transcription.
- **Traceable assessments:** inspect Q&A analysis, consolidated CV/interview findings, claim reviews, performance dimensions and interview quality using pinned source evidence.
- **Reviewed AI suggestions:** rehearse screening with synthetic CVs, review proposed job-description and criteria changes together, and explicitly confirm the selected changes before applying them.

Results support recruiter review. Evidence gaps, outdated results and processing failures are represented explicitly; scores are not hiring decisions.

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

## Getting started

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
Copy-Item .env.example .env
```

Copy the environment file only for a new checkout; preserve an existing `.env`. Configure it before starting services:

| Variables | Purpose |
| --- | --- |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database credentials; replace the example password. |
| `JWT_SECRET_KEY` | Private signing secret of at least 32 characters. |
| `GEMINI_API_KEY`, `GEMINI_CHAT_MODEL` | Recruiter chat credentials and model. |
| `GOOGLE_DRIVE_CREDENTIALS_HOST_PATH` | Absolute host path to an existing service-account JSON file; required by Compose. |
| `GOOGLE_DRIVE_CV_ROOT_FOLDER_ID` | Drive root used by CV intake. |
| `CV_MODEL_PROVIDER`, `CV_PARSER_MODEL`, `GEMINI_CV_MODEL` | CV inference settings. `auto` selects Gemini when a key is configured; `ollama` selects local CV inference. |
| `F5_MODEL_*`, `F6_MODEL_*` | Explicit Q&A analysis and consolidation settings; defaults use Gemini. |
| `PUBLIC_APP_URL` | Browser-facing origin; defaults to `http://localhost:5173`. |
| `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_MCP_SECRET` | Invitation delivery credentials. Use a Gmail app password and a separate MCP secret of at least 32 characters. |
| `DEEPGRAM_API_KEY` | Required when starting live transcription. |
| `LIVEKIT_API_URL`, `LIVEKIT_WS_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` | Interview media configuration; Compose provides same-PC development defaults. |
| `DISCORD_WEBHOOK_URL` | Optional approved-job posting integration. |

See [the environment example](.env.example) and [backend settings](backend/app/core/config.py) for supported options. Compose overrides database, Ollama, ChromaDB and internal MCP addresses for container networking.

### 2. Install dependencies and initialize storage

```powershell
docker compose build backend frontend
```

The image builds install Python dependencies from `backend/requirements.txt` and frontend dependencies using `npm ci`.

After configuring the credentials-file mount, generate a secret locally:

```powershell
docker compose run --rm --no-deps backend python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Copy it to `JWT_SECRET_KEY` in your private environment file. Generate a separate value for `EMAIL_MCP_SECRET` when enabling invitation delivery. Keep environment files and service-account credentials out of commits.

Start infrastructure, download the embedding model and apply database migrations:

```powershell
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
docker compose up -d
docker compose ps
```

| Service | Local address |
| --- | --- |
| Recruitment workspace | [localhost:5173](http://localhost:5173) |
| Interactive API documentation | [localhost:8000/docs](http://localhost:8000/docs) |
| API health | [localhost:8000/health](http://localhost:8000/health) |
| Database health | [localhost:8000/health/db](http://localhost:8000/health/db) |

Choose **Create an account** on the sign-in page to register a recruiter. Passwords require at least 12 characters. You can also create a staff account interactively:

```powershell
$staffEmail = Read-Host "Staff email"
docker compose exec backend python -m app.scripts.create_user --email $staffEmail --role recruiter
```

The script prompts for a password and confirmation. Use `--role interviewer` to create an interviewer account.

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

Live transcription requires Deepgram configuration. Candidate face comparison also requires model files; see the [candidate identification guide](docs/F7_CANDIDATE_IDENTIFICATION.md).

## Usage

1. Draft a job description with the assistant, review its criteria and weights, and approve it.
2. Connect its Google response Sheet and CV folder. The source worker imports applications automatically.
3. Select **Screen applications** to parse CVs, match criteria and calculate scores. Review evidence and processing issues before saving a shortlist.
4. Set interview times, preview recipient-specific invitations and confirm delivery.
5. Conduct the interview and inspect saved Q&A analysis, consolidated findings and post-interview assessments.
6. Review supported AI suggestions in the job workspace and confirm the exact selected changes before applying them.

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

Database and live-model checks use opt-in settings defined in their test modules. Configure the required services and environment variables before enabling these suites.

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

## Repository layout

```text
backend/
  app/             Routes, services, integrations, workflows and workers
  migrations/      Alembic database migrations
  tests/           Unit, integration and acceptance checks
frontend/
  src/             Recruitment workspace and shared UI
  tests/           Unit tests and browser smoke scripts
docs/              Architecture, progress and verification records
storage/           Local runtime artifacts; ignored by Git
compose.yaml       Development services and persistent volumes
```

See the [backend structure guide](backend/README.md) for module responsibilities.

## Contributing

Open an [issue](https://github.com/KaziRafiulKader09/Distill_AI/issues) to report a reproducible problem or discuss a feature. Pull requests should explain the behavior change, include relevant verification, and preserve source evidence, ownership checks and recovery behavior. Use synthetic candidate data in examples and tests.

## Maintainer

[KaziRafiulKader09](https://github.com/KaziRafiulKader09) · [GitHub repository](https://github.com/KaziRafiulKader09/Distill_AI)
