# Distill AI Frontend

The React/TypeScript client is the recruiter and candidate interface for Distill AI, an AI-powered recruitment and interview intelligence platform. It exposes saved evidence, processing progress and explicit decision controls across job preparation, screening, interviews and assessments.

Use the [project README](../README.md) for full-stack setup, feature scope, external services and privacy, and the [backend guide](../backend/README.md) for API/worker responsibilities. This client is the implemented application, not the original Vite starter.

## Screens and user journeys

| Route | Screen and behavior |
| --- | --- |
| `/` | Recruiter signup/login, session restoration and connection/expiration feedback. |
| `/recruiter/dashboard` | Owned recruitment summaries, attention/activity links and assistant dock. |
| `/recruiter/job-descriptions` | Chat, saved jobs, reference resources, criteria/weights, reviewed editing, approval, explicit posting and F11 suggestion review. |
| `/recruiter/cv-screening` | Source setup/import notifications, screening progress, search/filter/sort, profile/CV/evidence inspection, shortlist and confirmed application deletion. |
| `/recruiter/invitations` | Interview times, personalized previews, confirmed queueing, status and supported retry/reissue. |
| `/recruiter/interviews` | Recruiter devices, candidate profile/identity review, admission, media, transcript, question assistance and temporary notes. |
| `/recruiter/assessments` | F5/F6/post-interview findings, evidence, scoring settings, saved comparison, final selection and report/PDF controls. |
| `/interview#<private-token>` | Candidate instructions, joining window, devices, consent/countdown, waiting and admitted LiveKit interview. |
| `/interview-preview` | Development-only candidate UI preview, not a real authenticated interview. |

Candidates use private invitation links, not recruiter accounts. The database's `interviewer` role does not provide a separate delivered workspace; protected management features require a recruiter. Final selection records a human choice and sends no selection email or offer.

## Technology and component structure

React 19 supplies the component model; TypeScript describes view/API contracts; React Router manages route and URL-selected context. Vite 8 provides development/HMR and builds. Tailwind CSS 4 and shared/feature CSS define layout; LiveKit client/components supply media controls. `react-markdown` and `remark-gfm` display supported generated content.

```text
src/
  app/                  App shell, routes, sessions and navigation
  features/
    auth/               Account forms
    dashboard/          Recruitment summaries and assistant dock
    jd/                 Conversations, jobs, criteria and reviewed suggestions
    cv/                 Intake, screening, evidence/profile and deletion
    interviews/         Shortlist, invitations and candidate/recruiter media
    assessments/        Findings, scoring settings, comparison and reports
  components/           Shared chat, recruitment UI, dialogs and motion
  services/             HTTP/session and streaming helpers
tests/                  Node unit checks and Playwright smoke scripts
vite.config.ts          Plugins and development /api proxy
```

The browser displays backend-provided scores, versions and evidence rather than running a duplicate scoring pipeline. Route guards support navigation; backend authorization is the access boundary. Polling and streaming expose supported saved work states. Shared dialogs provide confirmation, focus/keyboard behavior and responsive layouts.

## Setup

### Full-stack Docker

Follow [root setup](../README.md#setup), then open [localhost:5173](http://localhost:5173). Compose builds with `npm ci`, mounts frontend source and uses a named `frontend_node_modules` volume. The frontend depends on a healthy API; individual integrations still need their configured providers.

### Host development with the Docker backend

Use Node.js 24 and npm. From the repository root, with the backend/supporting services configured:

```powershell
docker compose stop frontend
Set-Location frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Stopping the Compose frontend frees port 5173. Keep the backend's `PUBLIC_APP_URL` aligned with the browser origin. A healthy API does not imply that Google, models, SMTP or transcription are configured.

## Configuration and API connections

`vite.config.ts` reads these **process environment** values. Set them in the shell before starting Vite or through Compose's frontend environment. The current config does not call `loadEnv`, so do not assume adding server options to a frontend `.env` file loads them.

| Variable | Behavior |
| --- | --- |
| `API_PROXY_TARGET` | Defaults to `http://127.0.0.1:8000`; Compose sets `http://backend:8000`. |
| `DOCKER_WATCH_POLLING` | `true` enables polling for mounted source; Compose enables it. |
| `VITE_ALLOWED_HOST` | Optional exact public hostname. Use a hostname, not a full URL or blanket allow-all value. |

```powershell
$env:API_PROXY_TARGET = 'http://127.0.0.1:8000'
npm run dev
```

Browser requests use `/api/...`; the development proxy strips `/api` before forwarding. `changeOrigin: false` preserves the browser host for authentication-origin checks. Private model, Google, SMTP, Deepgram and LiveKit server secrets belong on the backend, never in client source or public `VITE_*` variables.

The API helper keeps access tokens in module memory, sends same-origin credentials, renews eligible expired sessions and handles binary PDF responses. Persistence uses a backend-issued HttpOnly session cookie. The candidate invitation fragment is a private bearer credential; avoid sharing it or exposing it in screenshots/logs.

## Usage details

- Select the job before reviewing its candidates, shortlist, assessments or suggestions. Supported search/filter/context state survives navigation.
- Source setup expects Name, Email, CV File and Image columns. Imports run on the backend every eligible minute even when this page is closed; screening is a separate action.
- Read criterion justifications and exact CV/transcript evidence before deciding. Missing evidence does not establish inability; unavailable and assessed-zero scores differ.
- Candidate comparison reuses saved completed assessments without starting analysis. Different criteria/policies or outdated results need explicit review. Default final projection is 30% CV and 70% F9 performance, subject to backend coverage/integrity gates.
- F11 uses isolated synthetic CVs. Selected description/criteria changes require a combined preview and explicit Apply; they never silently approve or publish a job.
- Delete candidate opens a confirmation modal for the selected job application. Active work can block deletion; a successful response can report pending file/index cleanup, which the backend retries.
- Identification requires current consent before comparison. Numerical similarity is recruiter-only. Decline/withdrawal/manual review are supported, and low similarity does not automatically reject anyone.
- Interview notes are temporary on-screen text, not saved interview records. Saved transcript evidence has a separate lifecycle.

## Verification

From `frontend`:

```powershell
npm test
npm run lint
npm run build
```

Tests use Node's runner with TypeScript stripping. Build runs TypeScript project compilation and writes `dist/`. Successful local checks do not establish real-provider/media acceptance.

Browser smoke scripts under `tests/*.smoke.mjs` need separately available Playwright and a supported browser; Playwright is not an application dependency. Supply it through external development tooling rather than silently changing the app lockfile. With an existing external installation:

```powershell
$env:NODE_PATH = 'C:/path/to/browser-tools/node_modules'
node tests/candidate-deletion.smoke.mjs
```

Read each script's prerequisites. The deletion script expects a dev server on port 5173 and mocks API requests. Other scripts serve `dist/` through fixture routing and need a prior build; several select Chrome or a script-specific executable setting. Fixture checks do not operate on real candidate records or establish live integration correctness.

## Build and deployment boundary

`npm run preview` can inspect the built bundle, but the current proxy is defined under Vite `server`; it is not a documented standalone production gateway. Deployments need SPA route fallback, a same-origin `/api` reverse proxy, compatible origin/cookie configuration, HTTPS and reachable media URLs. Private credentials remain on the backend. The Compose Vite server is a development service.

For temporary public testing, configure the backend `PUBLIC_APP_URL` and Vite allowed hostname, then recreate affected containers. A tunnel exposes frontend/API traffic but does not make loopback LiveKit media reachable from another device.

## Privacy and known limitations

The browser handles candidate contacts, CV evidence, transcripts and assessments returned by authorized APIs. Use synthetic identities in tests/screenshots. Media access requires browser permissions and a suitable secure context; photo-comparison consent is a separate application decision. Read the [root privacy table](../README.md#privacy-and-external-services) for model, speech, media, Google and SMTP transfers.

Application deletion retains other jobs, existing chat history, source originals, delivered mail, downloaded reports and backups. Pending cleanup is not completed physical erasure. Refreshing the page does not fix unavailable providers, stale evidence or stopped workers.

There is no separate candidate dashboard, interviewer/reviewer workspace, automatic offer/selection-email flow, MFA or password-reset screen. F10 cross-candidate process analysis and complete candidate-wide erasure are not implemented. F11's small single-pass suite does not establish statistical confidence. Real-device media, accessibility, recognition and production performance need representative validation. Vite may report a large-bundle warning; build success is not a latency guarantee. Some sample modes require synthetic fixtures absent from the reorganized docs folder.
