# F7: Candidate identification and profile overview

Implementation verified locally on 22 October 2026: 30 F7 checks (including
PostgreSQL lifecycle and installed-model rejection of a faceless image), seven
invitation checks, offline browser acceptance, TypeScript, lint, and all 25
frontend unit checks passed. Existing interview regression checks passed (27 run,
43 opt-in cases skipped). Migration `f4b830d927ae` is applied locally. The complete
backend imports successfully. The frontend build retains its bundle-size warning.
The updated flow also passes 13 interview-session checks, including real database
admission, reissued invitations and mocked media services.

Face similarity is available with the installed models. Automatic match verdicts require `FACE_MATCH_THRESHOLD`.
No real applicant photos or Google Form data were used for acceptance testing.

Implemented flow: read fixed response Sheet columns, link an application image, collect
waiting-room consent, compare one camera frame, and show the recruiter the result.
Existing experience, skills, saved CV score and areas to probe remain in the
candidate profile. Admission is always a recruiter action.

## Setup

From `backend/`:

```powershell
.venv/Scripts/python.exe -m pip install -r requirements.txt
.venv/Scripts/python.exe -m alembic upgrade head
.venv/Scripts/python.exe -m app.scripts.setup_face_models
```

The installer verifies the official OpenCV Zoo checksums for YuNet 2023mar and
SFace 2021dec and saves their licenses alongside the models. The model directory
defaults to `storage/face_models/` in the repository. `FACE_MODEL_PATH` overrides it.
Compose mounts this directory read-only into the backend; download on the host
before starting a rebuilt backend image.

Set `FACE_MATCH_THRESHOLD` in `.env` and restart the backend to enable automatic
match verdicts. Without a threshold, comparison still produces a recruiter-only
similarity score and requires recruiter judgment. Missing models offer manual review. OpenCV's
LFW example uses cosine similarity `0.363`; use that only for development
evaluation, not as evidence that a local deployment is calibrated. Test false
matches and false rejections using representative, consenting pairs before
selecting an operational threshold. A similarity value is not an identity
probability. No percentage confidence is shown to candidates.

## Form setup

Use these four required Google Form question / response Sheet column names:
`Name`, `Email`, `CV File`, and `Image`. Make both upload questions required in
Google Forms. Every imported response must contain a CV Drive link and exactly
one Image Drive link. Missing, invalid, or multiple image links reject that row;
no new candidate/application is created for it. Restrict Image uploads in the Form
to JPEG or PNG, at most 5 MB. Image decoding and face usability are checked during
consented verification. A required image upload does not require consent to face
comparison; candidates can still decline and use recruiter manual review.

`Timestamp` is optional. Extra columns are allowed and ignored. Header matching
ignores case and surrounding spaces; column ordering does not matter. Required
headers must be unique. Custom field labels and field-mapping controls are no
longer supported. Existing connected Sheets must have these four headers before
the next sync. Existing imported candidates, photos, consent and verification
records are preserved; removing this feature does not rewrite or delete them.

Share the image upload folder with the configured service account as well as the
response Sheet and CV folder. The image folder need not be inside the CV folder.
Sync applications after adding the required Image column and uploads. Distill
stores the Drive reference and reads image bytes only after explicit consent.
The old nullable `cv_sources.field_mapping` database column remains unused for
schema compatibility. No destructive database migration is needed.

## Waiting room and recruiter review

After checking devices, candidates see a modal pop-up with the versioned
explanation, retention date and accept/decline actions. Camera permission is not
consent. Agree and take my photo records consent first, then enables the selected
camera in the waiting-room video area. Consent v3 explains periodic face-count
checks while this preview is open; older consent must be renewed. The modal
closes before the camera starts and never contains a video feed. The page scrolls
to the top after consent and on retakes. Preview frames are checked about every
500 ms plus processing time, in memory, without downloading the reference photo,
extracting embeddings or consuming comparison attempts. The visible five-second
countdown starts only with exactly one detected face. Multiple faces, no face, or
stalled checks reset it; it restarts from five automatically when the candidate
is alone again. The exact final checked frame is sent for comparison. Candidates
can cancel at any time before comparison; processing stops after a result or on
leaving the waiting room. Camera checks use sampled frames, rather than observing
every video frame.
Retakes are explicit actions. No verification camera is opened before consent. Images are bounded to 5 MB and 16 million
pixels; processing resizes to at most 1280 pixels, requires one usable face, aligns
it, extracts SFace embeddings and compares cosine similarity on the backend.

Results are `matched`, `not_identified`, `unable_to_verify`, or `not_performed`.
Poor images and missing/multiple faces produce retry guidance. There are at most
five comparisons per enrollment, at least three seconds apart. After the fifth
attempt, candidates cannot capture or retake more photos, including after a page
reload. If the final result is not a match, both panels show that manual review
is required and admission stays blocked until the recruiter records that review.
A successful fifth comparison remains valid, but cannot be retaken. Preview
face-count checks do not consume attempts. Verification is
available before admission; it is never a hiring score or an automatic rejection.
The recruiter sees a polled result, raw cosine similarity scaled by 100 and the
configured threshold, clearly labeled as similarity rather than identity probability.
Only recruiter endpoints expose numerical scores. UI and backend block admission
until comparison is attempted, the candidate declines/withdraws, or a recruiter
records manual review. A low score never automatically rejects the candidate.
Admission checks use the current invitation and require the recruiter decision to
follow verification or manual review. Historical admission timestamps cannot block
consent/capture or grant entry after a new comparison. Candidates already admitted
do not reopen the pre-admission verification popup. The recruiter sees similarity
as a percentage (scaled cosine similarity, not identity confidence).
The recruiter can record manual review without changing the machine result. Reissuing an invitation resets consent, results and manual
review, preserving the reference and original expiry.

This checks resemblance to the submitted photo. It does not establish legal
identity, detect liveness, or continuously monitor an interview. No voice matching
or face gallery search is included.

## Data handling and deletion

- The source photo remains in protected Google Drive; Distill stores only its ID.
- Captured frames, source-image bytes and embeddings are processed in memory and
  are never saved to disk, database, vector indexes, or application logs.
- Consent version/time, similarity, threshold, exact model hashes, result/time,
  retry count and optional reviewer/note are application scoped. Recruiter access
  checks both job and application ownership; candidates use their active private
  invitation token. Private endpoints set `Cache-Control: no-store`.
- `IDENTITY_RETENTION_DAYS` defaults to 30 days from first linking. Reimport does
  not extend that expiry or silently replace a reference photo.
- The existing `cv_source_check_worker` purges expired references, consent details,
  matching metadata and manual notes. Run this worker for unattended cleanup.
  Reads also suppress and clear expired data.
- Candidates can withdraw and delete identification data, and recruiters have a
  corresponding delete control. A minimal application/expiry/deletion-state
  tombstone prevents a later Sheet sync from silently reenrolling the candidate.
  Enrollment after deletion requires a new application; no restore is implicit.
- These actions do not delete Google Form/Drive originals or backups. Source-file
  retention is managed by their owner, and database backups follow deployment
  retention. Full candidate deletion across CVs and vector indexes is separate.

## Verification

```powershell
# Unit/API checks
.venv/Scripts/python.exe -m unittest discover -s tests -p test_candidate_identification.py

# Opt-in PostgreSQL lifecycle checks using disposable synthetic rows
$env:RUN_F7_DB_TESTS = '1'
$env:RUN_F7_MODEL_TESTS = '1' # Also exercise the installed OpenCV models
.venv/Scripts/python.exe -c "import asyncio,unittest; asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy()); r=unittest.TextTestRunner().run(unittest.defaultTestLoader.discover('tests',pattern='test_candidate_identification.py')); raise SystemExit(not r.wasSuccessful())"
```

Actual Google Form/Drive access, representative face-pair calibration and a real
candidate/recruiter call remain deployment acceptance checks; fixture tests do
not establish face matching accuracy.
