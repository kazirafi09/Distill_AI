# Daily work — 22 October 2026

Project: Distill AI  
Timezone: Asia/Dhaka  
Status: Local implementation and verification; F7 backend rebuilt/reloaded in the existing development stack. No Git commit/push or production release claimed.

## 1. Reviewed Arif Hasan's JD207 assessment

Investigated the saved post-interview insights, transcript evidence and the code
that produces and presents the assessment. The review focused on whether findings
explained what the interview tried to assess and whether the conclusions were
supported by the actual answers.

Separated two sources of problems:

- Transcript limitations: missing speech, uncertain timing and cross-speaker order
  can make question/answer pairing or technical wording unreliable.
- Application behavior: analysis instructions, uncertainty validation, diagnostic
  visibility and presentation can be improved without pretending to repair the
  underlying recording.

The rerun review did not establish perfect assessment accuracy. A successfully
completed analysis can still require semantic review against the transcript and
technical references. Missing discussion is not proof of inability or a CV
contradiction. Work on the code was limited to useful evidence-handling and UI
improvements; transcript recognition quality remains a separate acceptance item.

## 2. Improved post-interview evidence handling

Updated the post-interview analysis guidance and validation to:

- Recheck original speech and the rest of an answer before drawing a conclusion
  from contradictory or ambiguous wording.
- Record material transcript uncertainty and explain its effect on a finding.
- Avoid silently correcting transcript text or assuming a recognition error was a
  candidate or interviewer mistake.
- Require matching project, time, role and scope for potential CV contradictions.
- Avoid inferring stuttering or other audio behavior from text alone.
- Disclose unverified timing/order and potentially missing speech.
- Retain safe validation diagnostics so a failed analysis can explain which check
  needs review; older runs without diagnostics are identified honestly.

New analyses use the revised prompt/evidence checks. Previously saved runs retain
their original results, and the UI explains when a run uses earlier checks.
Reports also carry the relevant post-interview evidence and limitations.

Relevant code:

- [Analysis and validation](../backend/app/services/interviews/post_interview_analysis.py)
- [Post-interview runs](../backend/app/services/interviews/post_interview_runs.py)
- [Post-interview reports](../backend/app/services/interviews/post_interview_report.py)
- [Insights UI](../frontend/src/features/assessments/PostInterviewAssessment.tsx)

## 3. Added one action for all post-interview insights

Added **Run all post-interview insights**, which requests these three analyses
together through the existing backend workflow:

1. CV claim review.
2. Interview performance.
3. Interview quality.

Individual rerun controls remain available for targeted recovery or review.
Required confirmation of the saved questions, scores and evidence remains beside
the action. This combined request should not be confused with automatic execution
of every stage from the initial F5 assessment button; that broader behavior is not
claimed completed by this change.

## 4. Redesigned the candidate assessment UI

Implemented the supplied layout direction using actual saved assessment data,
rather than embedding the sample candidate or example scores from the mockups.

The candidate detail view now has five tabs:

| Tab | What it presents |
| --- | --- |
| Summary | Final score, component scores, question-score matrix, strengths, weaknesses, evidence gaps and suggested verification topics. |
| CV findings | Filterable findings with expandable explanations and source evidence. |
| Interview Q&A | Question topics, criteria explored, saved answers, follow-ups, four score dimensions and reasons for the scores. |
| Criteria coverage | CV support alongside interview exploration; unasked criteria are not treated as weaknesses. |
| Verify next | Suggested follow-up topics with source evidence. |

Source evidence can open the relevant transcript turn. Run history remains scoped
to the candidate. The layout supports desktop and mobile screens, and score
formatting distinguishes an assessed zero from missing or unassessed evidence.
Long stored decimals now display at most two decimal places.

Relevant code:

- [Assessment workspace](../frontend/src/features/assessments/Assessments.tsx)
- [Candidate assessment](../frontend/src/features/assessments/CandidateAssessment.tsx)
- [Assessment styles](../frontend/src/features/assessments/CandidateAssessment.css)
- [View helpers](../frontend/src/features/assessments/assessmentView.ts)

## 5. Added the provisional final score

Changed the Summary's primary score card from the CV screening score alone to a
combined final score, with separate CV and interview performance values and a
contribution breakdown.

```text
Final score = 0.30 × saved CV score + 0.70 × saved interview performance score
```

The interview component comes from the saved F9 interview-performance assessment.
The F5 question-level relevance, accuracy, completeness and articulation marks
remain visible for evidence review; they are not averaged into an additional
component that counts the interview twice.

The policy requires:

- A valid saved CV score and completed interview-performance score.
- At least 80% assessed interview scoring weight.
- At least four assessed interview dimensions.
- Assessment of essential dimensions, including technical competency.

If these conditions are missing, the final score is incomplete rather than
invented. When available, it is labeled provisional and remains a recruiter review
aid. Essential CV review flags and transcript warnings are disclosed.

The projection uses the latest applicable interview-performance run, checks source
artifact integrity and F5 provenance, and does not rewrite saved source scores.
The detail view refreshes saved results so newly completed performance analysis
can update the displayed projection.

During the JD207 review, the displayed example was approximately **62.10/100**:
CV **78.67**, interview performance **55.00**. These are observed saved-result
values, not a fixed benchmark or a guarantee about future reruns. Calculation uses
stored precision before rounding the displayed result.

Relevant code:

- [Final-score policy and projection](../backend/app/services/interviews/final_score.py)
- [Assessment result integration](../backend/app/services/interviews/assessment_runs.py)
- [Frontend result contract](../frontend/src/features/assessments/api.ts)

## 6. Simplified reports and secondary details

Replaced the bottom accordion clutter with a visible **Report & export** button
and a **More details** menu.

Report & export opens a centered modal with report type, evidence options,
preview, print and PDF download. Technical report evidence requirements remain
enforced. More details provides access to the source transcript, run history,
saved assessment rationale and post-interview insights.

Dialogs support Escape, contained keyboard focus, background interaction blocking
and focus restoration after closing. The report layout is responsive.

Relevant code: [Report UI](../frontend/src/features/assessments/FullReport.tsx).

## 7. Matched the existing animations and controls

- Reused the Job Description tab navigation animation for assessment tabs,
  including the moving selected-tab indicator and content fade.
- Added modal opening animations using the existing motion tokens and keyframes.
- Preserved reduced-motion behavior.
- Removed the redundant **I reviewed this analysis** button from the header.
  Confirmation remains local to actions whose backend requires it.
- Fixed the saved-analysis dropdown's clipped value and missing chevron by giving
  it sufficient width and removing the clipping rule. It now uses the shared
  border, focus, picker and green selection styling and fits mobile screens.
- Corrected shared animation duration parsing: production CSS optimization can
  express milliseconds as seconds, and the JavaScript animation hook now handles
  both units correctly.

Relevant code: [Shared workspace motion](../frontend/src/components/motion/useWorkspaceMotion.ts).

## 8. Updated local preview configuration and progress documentation

Updated the local `.env` public application URL and Vite allowed-host variable to
the active Cloudflare tunnel used during today's work. The tunnel is temporary;
recheck it before reuse. Configuration values and credentials are not copied into
this work log, and previously delivered invitation links are not rewritten.

Updated [PROJECT_PROGRESS.md](PROJECT_PROGRESS.md) with the 22 October checkpoint,
assessment implementation details, scoring policy, verification results and
remaining acceptance boundaries. Earlier dated history was preserved.

## 9. Added F6 side-by-side candidate comparison

Added the Assessments comparison modal for two candidates with completed F6
results. It preserves the exact selected runs across filtering/pagination, keeps
sample and real candidates separate, and reuses saved evidence without starting
model calls. The view includes component/combined scores, performance dimensions,
criteria coverage, findings and evidence, with differing criteria/scoring versions
and unavailable data disclosed. Desktop/mobile browser checks and four comparison
unit checks passed; the frontend suite reached 25 passing tests. Real-data
comparison acceptance remains open.

Relevant code: [Comparison modal](../frontend/src/features/assessments/CandidateComparison.tsx)
and [comparison checks](../frontend/tests/assessment-comparison.test.mjs).

## 10. Implemented F7 candidate identification

Added application-linked reference photos, versioned consent, in-memory one-to-one
YuNet/SFace comparison, retry limits, recruiter status and manual review, and
retention/deletion. Existing profile information is reused: name, experience,
skills, saved CV score and areas to probe. Face similarity is separate from hiring
scores and does not cause automatic rejection.

Final candidate flow: after device check, a modal explains how the application
photo and camera frame are used. **Agree and take my photo** saves consent v2,
then enables the selected camera and automatically captures exactly one frame
when video is ready. There is no verification capture before consent. Retakes
are explicit; decline, camera-error and manual-review paths remain available.
Previous consent requires the updated explanation before a new comparison.

The recruiter sees the result, cosine similarity scaled by 100, and threshold
when configured. Scores are available only through recruiter-owned endpoints and
are labeled as similarity rather than identity probability. Without a calibrated
`FACE_MATCH_THRESHOLD`, comparison still returns the numerical score for recruiter
judgment, without an automatic match verdict.

Both UI and backend prevent admission while the consent/comparison step is
pending. A completed comparison attempt, declined/withdrawn consent or recorded
manual review lets the recruiter make the admission decision. Reissued
invitations reset old consent/results/review so they cannot authorize a new join.

Frames and embeddings are never persisted. The source photo remains in protected
Google Drive; Distill retains only the reference and application-scoped metadata.
The default 30-day retention, worker cleanup, withdrawal/deletion controls and
minimal tombstones prevent silent reenrollment through later Sheet syncs.

Implementation details and limits: [F7 guide](F7_CANDIDATE_IDENTIFICATION.md).

## 11. Simplified Form import to four mandatory fields

The first F7 iteration added field mapping. The user then requested its removal,
so the final implementation uses these fixed response columns:

| Column | Required behavior |
| --- | --- |
| `Name` | Required candidate name. |
| `Email` | Required valid email address. |
| `CV File` | Required CV upload linked through Google Drive. |
| `Image` | Required single image upload linked through Google Drive; Form upload settings should allow JPEG/PNG, at most 5 MB. |
| `Timestamp` | Optional. |

Case and surrounding header spaces are ignored; columns may be reordered. Extra
Form questions are allowed and ignored. Missing/invalid/multiple Image links
reject the response without creating a new application. Image decoding and face
quality are checked during consented verification.

Removed **Edit Form field mapping**, its preview/selection UI and preview API.
Existing imported candidates and identification records were preserved. The old
nullable database mapping column remains unused for schema compatibility; no
destructive migration was needed. Existing Sheets need all four required headers
and per-response uploads before the next sync.

## 12. Prepared a manual project walkthrough

Provided a sample Mid-Level Python Backend Developer JD prompt and explained the
required Form field names and sharing of the response Sheet/CV/image folders.
The user chose to operate the app manually. Created a one-page fictional Sami
Rahman CV as a selectable-text PDF, verified its extracted content and visually
checked the rendered page. It includes relevant Python/FastAPI/PostgreSQL/pytest/
Docker experience and explicit AWS/CI-CD limits for interview follow-up.

Test artifact: `output/pdf/Sami_Rahman_Python_Backend_CV.pdf`.
This prepares the walkthrough; it does not establish that the user completed a
new job, Form submission, screening, invitation delivery or real interview.

## 13. Activated the updated F7 development runtime

Checked the active 22 October Cloudflare tunnel against `.env` and the running
frontend/backend/invitation worker. `PUBLIC_APP_URL` and `VITE_ALLOWED_HOST` already
matched, so no URL rewrite or tunnel restart was needed. The source photo access
uses the existing configured Google Drive service account.

The running backend lacked OpenCV and the model mount. Rebuilt and recreated
only the backend service with its declared dependencies and mounted YuNet/SFace
models. Verified comparison availability, consent v2, backend database health and
public frontend/API HTTP 200 after reload. The Cloudflare tunnel and existing
invitation worker stayed running; no email was sent or requeued.

This is an update to the existing development stack, not a production release or
real-device acceptance. Credentials and invitation tokens are omitted from this log.

## 14. Verification completed

| Check | Result | Scope |
| --- | --- | --- |
| Final-score tests | 6 passed | Focused combination, evidence-gate and source/projection checks. |
| Post-interview analysis tests | 10 passed | Focused evidence, ambiguity and validation behavior. |
| Frontend unit checks | 25 passed | Includes four comparison checks plus score semantics, findings/history/coverage helpers and existing regressions. |
| `npm run build` | Passed | Production TypeScript/Vite build; existing large-bundle warning remains. |
| `npm run lint` | Passed | Frontend ESLint. |
| Assessment browser smoke | Passed; no browser errors | Local fixture APIs, desktop/mobile layouts, five tabs, expanded evidence, transcript navigation, history, review gates, combined-analysis request, report modal/preview/download/print, animations and reduced motion. |
| F7 API/image/database/model checks | 22 passed | Fixed required fields, consent versions, image limits, ownership, score privacy, retries, retention/deletion, reimport handling and admission sequencing. |
| Interview-session checks | 13 passed | Real PostgreSQL lifecycle/admission/reissue checks and HTTP contracts, with media services mocked. |
| Invitation checks | 7 passed | Invitation lifecycle checks; repaired stale test fixtures and covered F7 reset queries. |
| F7 browser acceptance | Passed | Consent modal, no frame before consent, exactly one automatic capture, retakes, mobile layout, withdrawal, fixed Form fields, recruiter score and manual review. |
| Development runtime | Passed | Backend rebuilt; OpenCV/models available; consent v2 active; public frontend/API HTTP 200 and database connected. |
| Whitespace checks | Passed | Checked code changes during UI verification. |

Verification assets:

- [Final-score tests](../backend/tests/test_final_score.py)
- [Post-interview analysis tests](../backend/tests/test_post_interview_analysis.py)
- [Assessment view tests](../frontend/tests/assessment-view.test.mjs)
- [Browser smoke](../frontend/tests/assessment-redesign.smoke.mjs)
- [F7 checks](../backend/tests/test_candidate_identification.py) and [browser smoke](../frontend/tests/f7.smoke.mjs).
- Screenshots: `output/assessment-redesign/` and `output/f7/`.

The browser smoke uses mocked API data and local production assets. It does not
establish live model correctness, real PDF content acceptance, inbox delivery or
real-device media quality. Test counts above describe separate focused checks,
not a complete application regression run.

## 15. Remaining work

- Complete the manual JD -> required Form uploads -> CV screening -> invitation ->
  consent/capture -> recruiter review/admission -> interview -> assessment flow.
- Verify actual Google Form/Drive permissions and reference-photo availability,
  camera access, cross-device behavior and recruiter score visibility.
- Evaluate consenting representative same/different-person image pairs and choose
  a calibrated automatic matching threshold. Software checks do not establish
  biometric accuracy or liveness protection.

- Calibrate model findings with recruiter-reviewed real transcripts and technical
  references, including what each question intended to assess.
- Improve or verify transcript recognition, speaker attribution and common timing
  where the original speech is unreliable or missing.
- Complete real-data report review and broader end-to-end acceptance.
- Confirm any future requirement to automatically chain initial assessment,
  consolidation and all post-interview analyses; the combined insights action is
  the behavior implemented here.
- Retain the distinction between software validation and the correctness of a
  hiring judgment. No automatic rejection policy was added.

Migration `f4b830d927ae` was added and applied locally for F7. The development
backend was rebuilt/reloaded and its public health was verified. No Git
commit/push, production release or Redmine update was performed. Earlier dated
work and existing local changes were preserved.
