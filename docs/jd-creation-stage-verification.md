# JD creation stage verification

Implemented on `module/m03-cv-screening-scoring`, without commits or pushes.
The working tree was clean before implementation. Both the Alembic script head
and running PostgreSQL revision were verified as `a74d2bf5c901`. The additive
migration is `b83c1d6e902a`, and it has been applied to the local Docker database.
Existing JDs retain null profile/version/form fields; no backfill is performed.

## PowerShell commands

Run from the existing repository with its configured `.env` and Docker services:

```powershell
Set-Location D:\Distill_AI
docker compose up -d
docker compose exec backend alembic heads
docker compose exec backend alembic upgrade head
docker compose exec backend alembic current
docker compose exec backend alembic check
docker compose exec backend python -m compileall -q app
docker compose run --rm --no-deps -e RUN_JD_DB_TESTS=1 -v "${PWD}/backend/tests:/workspace/backend/tests:ro" backend python -m unittest discover -s tests -v
docker compose exec frontend npm run build
docker compose exec frontend npm run lint
Invoke-RestMethod http://localhost:8000/health/db
```

Expected migration head/current: `b83c1d6e902a`. `alembic check` should report
no new upgrade operations. Integration tests roll back their own fixture rows
and delete only their UUID-named test checkpoint thread. PostgreSQL identity
sequences can advance during these rolled-back tests. No LLM calls, retrieval,
indexing, posting or candidate processing occur in this suite.

## Manual checklist

- Open http://localhost:5173 and sign in as an existing recruiter. In the central
  chatbot, request a new JD with only a role. Confirm each turn asks at most two
  related questions, and supplied answers are not repeatedly requested.
- Use this role: Junior Python Developer; responsibility: assist senior
  developers; technical skills: knowledge of Python (Must have), Docker
  familiarity (Preferred); work experience: no minimum years, freshers eligible;
  qualifications: BSc in CSE (Must have); additional criterion: English
  communication (Preferred). Confirm no experience, equivalent degree or new
  duties are invented. Explicitly state no work experience criterion if freshers
  should be eligible without experience; confirm that dimension remains addressed.
- In a separate conversation supply `minimum 1.5 years of relevant work
  experience` and `freshers are not eligible`. Check the exact wording in the
  structured profile and advertisement. Revise these to `no minimum years`
  and `freshers eligible`; verify the earlier restrictions disappear together.
- Supply a skill without a priority. Confirm clarification is requested and an
  early draft, if requested, cannot be accepted with unresolved priorities.
- Ask for a suggested weight distribution. Confirm the displayed proposal is
  not confirmed until you explicitly accept it. Alternatively supply weights
  yourself. Use technical skills 60%, qualifications 25%, additional criteria
  15% for the example without an experience criterion. Zero weights are allowed;
  dimensions without criteria can have only zero weight. Try a total of 90%
  and confirm acceptance is blocked until corrected to 100%.
- Draft before creating a form. Confirm the draft and criteria/priority/weight
  panel are visible, Accept is disabled with an explanation, Cancel remains
  available, and internal weights/criterion IDs are absent from the advertisement.
- Revise using a non-form URL, HTTP URL, Google Form edit URL or lookalike domain.
  Confirm acceptance remains blocked. Add your actual public form URL using
  `https://forms.gle/<id>`, `https://docs.google.com/forms/d/<id>/viewform`, or
  `https://docs.google.com/forms/d/e/<id>/viewform`. Confirm it appears in the draft
  and the UI describes format validation without asserting accessibility or
  correct configuration. Public view URLs may contain a query string.
- Make at least five revisions. Edit a criterion, priority, experience wording,
  weights and form URL. Confirm the same criterion keeps its ID when edited,
  new criteria receive new IDs, deleted criteria disappear, revision_count
  increases, and no revision limit appears. Add/remove a dimension and confirm
  the new weight distribution requires confirmation. Reload during gathering
  and review to confirm PostgreSQL checkpoint restoration.
- Accept the complete draft. Confirm it saves once, appears in Your Job
  Descriptions and reloads correctly. Cancel another draft and confirm no JD is
  saved. Open an old JD and confirm its original text remains readable.
- Use a second recruiter to request the first recruiter's JD/conversation URLs;
  expect 404. Unauthenticated requests must remain rejected.
- In browser DevTools, inspect the response and replay approval on both endpoints
  below using their current checkpoint and interrupt IDs. For missing form,
  unconfirmed priorities/weights, invalid total, or stale draft/profile, expect
  422 even if the UI is bypassed. A stale checkpoint/interrupt must return 409.
  Approval retry after successful save must reuse the same document; the
  automated PostgreSQL test also simulates failure after graph approval and
  proves recovery from persisted checkpoints after rebuilding the graph.

Direct JD action:

```text
POST /jd/conversations/{jd_conversation_id}/actions
Authorization: Bearer <recruiter token>
{"checkpoint_id":"<JD checkpoint>","interrupt_id":"<interrupt>","action":"approve"}
```

Central chatbot action:

```text
POST /assistant/conversations/{assistant_conversation_id}/jd-actions
Authorization: Bearer <recruiter token>
{"checkpoint_id":"<assistant checkpoint>","jd_checkpoint_id":"<JD checkpoint>","interrupt_id":"<interrupt>","action":"approve"}
```

For database verification, open psql using the container's existing environment:

```powershell
docker compose exec postgres sh -c 'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

Run this SQL in psql, then `\q` to exit:

```sql
SELECT number, title, profile_version, google_form_url, screening_profile
FROM job_descriptions ORDER BY created_at DESC LIMIT 10;
```

Accepted new JDs should contain the reviewed profile, positive profile version
and matching URL. Legacy rows should remain null for those three fields.

This stage includes JD creation only. CV parsing, scoring, ranking, MCP and new
ChromaDB indexing have not been implemented. Live model conversation quality
and browser interaction remain items for recruiter verification above.
