# Distill AI

AI-powered CV screening, candidate scoring and interview assessment.

## Current foundation

- React, Vite and TypeScript frontend.
- FastAPI backend with PostgreSQL connectivity.
- PostgreSQL 16 through Docker Compose with persistent storage.
- Staff account creation, password hashing, JWT login and role restrictions.
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
