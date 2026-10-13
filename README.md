# Distill AI

AI-powered CV screening, candidate scoring and interview assessment.

See the [project progress tracker](docs/PROJECT_PROGRESS.md) for current features,
project flows, verification status, and remaining work.

## Repository layout

- `backend/`: FastAPI API, feature services, integrations, workflows, workers,
  database models, migrations, and tests. See the
  [backend structure guide](backend/README.md) for module responsibilities.
- `frontend/`: React application, organized into features and shared components.
- `docs/`: Architecture diagrams, planning artifacts, and verification guides.
- `storage/`: Local runtime files; downloaded CVs are ignored by Git.
- `compose.yaml`: Development services and persistent Docker volumes.

## Current foundation

- React, Vite and TypeScript frontend.
- FastAPI backend with PostgreSQL connectivity.
- PostgreSQL 16 through Docker Compose with persistent storage.
- Staff account creation, password hashing, JWT login and role restrictions.
- Recruiter signup and persistent, revocable browser sessions (seven days by default).
- Application logging, error handling and health endpoints.
- Alembic migration for the users table.

FastAPI and React currently run locally. PostgreSQL runs in Docker.

## Local setup — Windows PowerShell

Requirements: Python 3.12, Node.js 24, npm and Docker Compose.

Run from the repository root:

```powershell
Copy-Item .env.example .env
```

For a new setup only: edit `.env` with local database credentials and a
privately generated JWT secret. Keep an existing `.env`; do not overwrite it.
Never commit `.env`.

```powershell
python -m venv backend/.venv
.\backend\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
docker compose up -d postgres
docker compose ps
```

Wait for PostgreSQL to become healthy, then initialize the database:

```powershell
cd backend
python -m alembic upgrade head
```

Create a staff account when needed:

```powershell
python -m app.scripts.create_user --email recruiter@example.com --role recruiter
```

The script prompts for a password and confirmation.
Passwords require at least 12 characters.
Supported roles: recruiter and interviewer.

Recruiters can also choose **Create an account** on the sign-in page. Signup
requires a password of at least 12 characters and signs the new recruiter in.
An HttpOnly cookie restores the session after reloads and browser restarts;
short-lived access tokens renew automatically. Sign-out revokes that browser's
session. Configure `AUTH_SESSION_DAYS` (default 7) and
`ACCESS_TOKEN_EXPIRE_MINUTES` (default 30) in `.env`. Set `PUBLIC_APP_URL` to the
actual public frontend origin; HTTPS deployments receive Secure cookies.
Local Vite access from localhost is also supported. Apply `alembic upgrade head`
to create the session table before using the updated login.

Start the backend:

```powershell
python -m uvicorn app.main:app --reload
```

In another terminal, from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

Frontend: http://localhost:5173
API documentation: http://127.0.0.1:8000/docs

## Verification

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/db
```

Expected: application status `ok` and database `connected`.

Authentication and role checks passed in Swagger on October 2.
On October 3, dependency checks, backend/database health and the users
migration passed on the home PC.

Accounts belong to each local database and must be created separately.

## Incremental M02 scope

M02 remains partially complete. Upcoming increments will add:

- M03 records for jobs, candidates, CVs, applications and evaluations.
- Persistent processing status, duplicate prevention, retries and recovery.
- Candidate data deletion behavior.
- Full application containerization and remaining reproducibility checks.
- Interview-specific storage and processing as later modules require them.

## M03 development order

1. Implement the required storage and processing support.
2. Build F1–F2 LangGraphs.
3. Test extraction, evidence, scoring, ranking and interruption handling.
4. Connect the verified workflows to the UI.

CV ingestion follows Google Form → Google Drive → Distill AI.

The CV parsing stage adds local Tesseract OCR, Qwen3 4B extraction, versioned
PostgreSQL results and authenticated MCP tools. Setup commands, limits and
verification are in [the CV parsing guide](docs/cv-parsing-stage-verification.md).

CV Screening now starts durable evidence indexing, MCP retrieval, semantic
matching, deterministic scoring and ranking runs. The central chatbot remains
unchanged. See [the screening setup and verification guide](docs/cv-screening-stages-verification.md)
for the initial rubric, worker startup, recovery and review limitations.
