"""Owned, read-only report projection. No model calls or score recalculation."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.assessments import AssessmentResult
from app.models.cv import Candidate
from app.models.interviews import InterviewSession, InterviewInvitation, InvitationEmailJob
from app.services.interviews.assessment_runs import owned_run, public_run
from app.services.interviews.rubric import DIMENSIONS
from app.services.interviews.analysis import digest
from app.services.interviews.transcript_preparation import ordered_turns
from app.services.recruitment_access import owned_job, owned_application


def label(value):
    return str(value).replace("_", " ").capitalize()


def score(value):
    from app.services.interviews.recruiter_report import number
    return "Not assessed" if value is None else f"{number(value)}/100"


def moment(value):
    if not value:
        return "Not recorded"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(ZoneInfo("Asia/Dhaka")).strftime("%d %b %Y, %H:%M:%S (Asia/Dhaka)")


def clock(ms):
    if ms is None:
        return "Timing unavailable"
    seconds = max(0, ms or 0) // 1000
    return f"{seconds // 60}:{seconds % 60:02d}"


async def document(run_id, recruiter_id, report_type="recruiter", include_evidence=False):
    if report_type not in {"recruiter", "technical"}:
        raise HTTPException(422, "Choose a recruiter or technical report.")
    async with SessionFactory() as db:
        run = await owned_run(db, run_id, recruiter_id)
        if run.status != "completed":
            raise HTTPException(409, "A full report is available after this assessment completes.")
        saved = await db.get(AssessmentResult, run.id)
        artifact = saved.payload if saved else {}
        if not artifact or artifact.get("artifact_sha256") != digest({k: v for k, v in artifact.items() if k != "artifact_sha256"}):
            raise HTTPException(409, "The saved assessment report failed its integrity check.")
        job = await owned_job(db, run.job_id, recruiter_id)
        parent = await owned_run(db, run.parent_run_id, recruiter_id) if run.kind == "f6" else run
        if parent.job_id != run.job_id or parent.application_id != run.application_id or parent.synthetic != run.synthetic:
            raise HTTPException(409, "Report sources are incompatible.")
        # F6 already froze its full accepted F5 and CV inputs. Never replace them with newer results.
        f5 = run.inputs["f5"] if run.kind == "f6" else artifact
        cv = run.inputs.get("cv") if run.kind == "f6" else run.inputs.get("cv_snapshot")
        candidate = {"name": run.label, "email": None, "application_id": None}
        session = invitation = delivery = None
        application = None
        if not run.synthetic:
            application = await owned_application(db, run.job_id, run.application_id, recruiter_id)
            person = await db.get(Candidate, application.candidate_id)
            candidate = {"name": person.full_name, "email": person.email, "application_id": str(application.id)}
            session = await db.scalar(select(InterviewSession).where(InterviewSession.application_id == application.id,
                InterviewSession.job_description_id == job.id, InterviewSession.recruiter_id == recruiter_id))
            invitation = await db.scalar(select(InterviewInvitation).where(InterviewInvitation.application_id == application.id,
                InterviewInvitation.job_description_id == job.id, InterviewInvitation.recruiter_id == recruiter_id))
            if invitation:
                delivery = await db.scalar(select(InvitationEmailJob.status).where(InvitationEmailJob.invitation_id == invitation.id))
        metadata = {"candidate": candidate, "job": {"id": str(job.id), "number": job.number, "title": job.title},
            "run": public_run(run), "generated_at": datetime.now(timezone.utc).isoformat(),
            "outdated": job.profile_version != run.inputs["profile_version"] or digest(job.screening_profile) != digest(run.inputs["profile_snapshot"])}
        if report_type == "recruiter":
            from app.services.interviews.recruiter_report import assemble as recruiter_document
            return recruiter_document(metadata, run.inputs, f5, artifact if run.kind == "f6" else None, cv, include_evidence)
        report = assemble(metadata, run.inputs, f5, artifact if run.kind == "f6" else None, cv, parent,
            application=application, session=session, invitation=invitation, delivery=delivery)
        report["report_type"] = "technical"
        report["filename"] = f"distill-assessment-{run.id}-technical.pdf"
        return report


def assemble(meta, inputs, f5, f6, cv, parent, *, application=None, session=None, invitation=None, delivery=None):
    sections = []
    evidence_entries = []
    evidence_keys = {}
    evidence_by_criterion = {}
    criteria = {c["id"]: c["text"] for c in inputs["criteria"]}
    prepared = inputs["prepared"]
    turns = ordered_turns(prepared["turns"])
    text_only = prepared.get("text_only", False)
    by_turn = {t["id"]: t for t in turns}

    def section(title):
        blocks = []
        sections.append({"title": title, "blocks": blocks})
        return blocks

    def add(blocks, text, kind="text"):
        if text is not None:
            blocks.append({"kind": kind, "text": str(text)})

    def quotes(blocks, evidence, criterion_ids=()):
        for e in evidence:
            if e.get("turn_id"):
                turn = by_turn.get(e["turn_id"], {})
                kind = "Interview"
                origin = (f"Interview | {label(turn.get('speaker_role', 'Not recorded'))} | turn {e['turn_id']} | "
                    f"{clock(turn.get('session_start_ms', e.get('session_start_ms', 0)))}-"
                    f"{clock(turn.get('session_end_ms', e.get('session_end_ms', 0)))} | "
                    f"transcript version {prepared.get('source_version') or prepared.get('fixture_revision') or 'Not recorded'}")
            else:
                kind = "CV"
                origin = f"CV {e.get('source_id')} | characters {e.get('start')}-{e.get('end')} | file {e.get('file_version_id')} | parse {e.get('parse_id')}"
            key = digest({"origin": origin, "quote": e["quote"]})
            if key not in evidence_keys:
                evidence_keys[key] = f"E{len(evidence_entries) + 1}"
                evidence_entries.append({"id": evidence_keys[key], "kind": kind, "source": origin, "quote": e["quote"]})
            citation = evidence_keys[key]
            blocks.append({"kind": "quote", "text": e["quote"], "citation_id": citation})
            add(blocks, f"[{citation}] {origin}", "note")
            for criterion_id in criterion_ids:
                evidence_by_criterion.setdefault(criterion_id, set()).add(citation)

    b = section("1. Assessment overview")
    add(b, f"Candidate: {meta['candidate']['name']}", "heading")
    if meta["candidate"]["email"]:
        add(b, f"Email: {meta['candidate']['email']}")
    add(b, f"Job {meta['job']['number']}: {meta['job']['title']}")
    add(b, f"Report generated: {moment(meta['generated_at'])}")
    add(b, "SYNTHETIC DEVELOPMENT SAMPLE - not a real candidate assessment." if meta["run"]["synthetic"] else "Confidential candidate assessment - authorized recruiter review.", "warning")
    add(b, "AI scores and conclusions are provisional and require human review. CV and interview scores are separate. This report does not make a hiring decision.", "warning")
    add(b, "Score summary", "heading")
    add(b, f"CV documented credit: {score(cv['score'].get('aggregate')) if cv else 'Not available in this assessment'}")
    for d in DIMENSIONS:
        summary = f5["dimension_summaries"][d]
        add(b, f"Interview {label(d)}: {score(summary['score_100'])} ({summary['assessed_count']} assessed; {summary['not_assessable_count']} not assessed)")
    add(b, "Contents: assessment overview; job criteria; recruitment timeline; CV screening; interview scores and full criterion coverage; detailed Q&A; consolidated findings; complete saved transcript; provenance; numbered evidence appendix." if text_only else "Contents: assessment overview; job criteria; recruitment timeline; CV screening; interview scores and full criterion coverage; detailed Q&A; consolidated findings; complete chronological transcript; provenance; numbered evidence appendix.")
    if text_only:
        add(b, prepared["timing_warning"], "warning")
        add(b, f"Final saved text: {len(turns)} segments; {len(f5['results'])} question groups. No reliable shared transcript times or speaking duration are available.")
    else:
        add(b, f"Recorded transcript span: {clock(turns[0]['session_start_ms'])} to {clock(max(t['session_end_ms'] for t in turns))}; {len(turns)} segments; {len(f5['results'])} question groups. This is the transcript span, not measured speaking time.")
    if f6:
        add(b, f"Suitability: {label(f6['findings']['suitability']['position'])}", "heading")
        add(b, f6["findings"]["suitability"]["rationale"])
    else:
        add(b, "F6 consolidation is not included in this F5 report. Generate and open an F6 result for consolidated findings.", "warning")
    if meta["outdated"]:
        add(b, "The job criteria have changed since this assessment. Scores below use the saved assessment criteria.", "warning")

    b = section("2. Job requirements used for assessment")
    add(b, f"Saved criteria version: {inputs['profile_version']}. Job title is current metadata; the criteria below are the frozen assessment inputs.", "note")
    for c in inputs["criteria"]:
        add(b, c["text"], "heading")
        add(b, f"{label(c.get('dimension', 'unspecified'))} | {c.get('priority', 'Not recorded')} | {c['id']}", "note")

    b = section("3. Recruitment and interview timeline")
    add(b, "Operational dates reflect records available at export. Invitation creation is not proof of email delivery. Missing events are shown explicitly.", "note")
    add(b, f"Application imported: {moment(getattr(application, 'created_at', None))}")
    if application and application.submitted_at_text:
        add(b, f"Application submitted (source text): {application.submitted_at_text}")
    add(b, f"Invitation created: {moment(getattr(invitation, 'created_at', None))}; email delivery status: {delivery or 'Not recorded'}")
    for title, field in (("Scheduled start", "starts_at"), ("Scheduled end", "ends_at"), ("Interview started", "started_at"),
                         ("Candidate admitted", "candidate_admitted_at"), ("Interview completed", "completed_at")):
        add(b, f"{title}: {moment(getattr(session, field, None))}")
    add(b, f"Interview status: {getattr(session, 'status', 'Synthetic fixture' if prepared['synthetic'] else 'Not recorded')}")
    add(b, f"Transcript finalized: {moment(prepared.get('finalized_at'))}; source status: {prepared.get('source_status', prepared.get('status', 'Not recorded'))}")
    add(b, f"Transcript quality flags: {', '.join(prepared.get('source_quality_flags', [])) or 'None recorded'}")
    add(b, f"F5 created: {moment(parent.created_at)}")
    add(b, f"Selected {meta['run']['kind'].upper()} result saved: {moment(meta['run']['updated_at'])}")
    if prepared["synthetic"]:
        add(b, "Synthetic timestamps are fixture-relative. No real application, invitation or interview session is attached.", "warning")

    b = section("4. Saved CV screening")
    if not cv:
        add(b, "No CV assessment was pinned to this F5 run. No newer screening result has been substituted.", "warning")
    else:
        add(b, f"CV assessment: {cv['id']}", "note")
        if cv.get("synthetic"):
            add(b, "Authored synthetic screening decisions; this is not a model-screening benchmark.", "warning")
        add(b, f"Documented CV credit: {score(cv['score'].get('aggregate'))}", "heading")
        add(b, cv["score"].get("note"))
        for d in cv["score"].get("dimensions", []):
            add(b, f"{label(d['dimension'])}: {score(d.get('score'))}; approved weight {d.get('approved_percentage')}%")
        for flag in cv["score"].get("must_have_review_flags", []):
            add(b, f"Must-have review flag: {flag}", "warning")
        for m in cv.get("matches", []):
            add(b, criteria.get(m["requirement_id"], m["requirement_id"]), "heading")
            add(b, f"Saved decision: {label(m['decision'])}. {m['justification']}")
            for missing in m.get("unsupported_requirement_parts", []):
                add(b, f"Unsupported requirement part: {missing}", "note")
            quotes(b, m.get("evidence", []), [m["requirement_id"]])

    b = section("5. Interview scoring and requirement coverage")
    add(b, "Each dimension averages its assessed 0-4 group scores and scales to 0-100. Unknown scores are excluded; assessed zeros are included. No combined CV/interview score is calculated.")
    for d in DIMENSIONS:
        s = f5["dimension_summaries"][d]
        add(b, f"{label(d)}: {score(s['score_100'])}; {s['assessed_count']} assessed, {s['not_assessable_count']} not assessed, {s['total_groups']} total groups.", "heading")
    add(b, "Explored means discussed, not verified proficiency. Unasked criteria carry no interview performance score.", "note")
    for c in f5.get("coverage", []):
        add(b, f"{criteria.get(c['criterion_id'], c['criterion_id'])}: {label(c['exploration'])}; groups {', '.join(c['group_ids']) or 'None'}")

    b = section("6. Detailed interview questions and answers")
    add(b, "Groups use saved text and conversational context. Segment save order is not verified cross-speaker chronology." if text_only else "Groups bring together original questions, follow-ups, clarifications and later revisits. Within each group, segments retain chronological order. See section 8 for the uninterrupted interview sequence.", "note")
    roles = (("question_turn_ids", "Question"), ("follow_up_turn_ids", "Follow-up"), ("clarification_turn_ids", "Clarification"),
             ("revisit_turn_ids", "Later revisit"), ("answer_turn_ids", "Answer"))
    for result in f5["results"]:
        g = result["group"]
        add(b, f"{g['id']}: {g['topic']}", "heading")
        add(b, f"Answer state: {label(g['answer_state'])}. Requirements: " + "; ".join(criteria.get(cid, cid) for cid in g["criterion_ids"]))
        membership = {}
        for field, role in roles:
            for tid in g[field]:
                membership.setdefault(tid, []).append(role)
        for t in turns:
            if t["id"] in membership:
                add(b, f"[{clock(t['session_start_ms'])}-{clock(t['session_end_ms'])}] {'Candidate' if t['speaker_role'] == 'candidate' else 'Interviewer'} / {', '.join(membership[t['id']])}", "note")
                add(b, t["text"], "quote")
        for d in DIMENSIONS:
            e = result["evaluations"][d]
            add(b, f"{label(d)}: {score(e['score_100'])}", "heading")
            add(b, e["justification"])
            add(b, f"Reason: {label(e['reason'])}; accuracy basis: {label(e.get('accuracy_basis', 'Not recorded'))}", "note")
            if e.get("accuracy_warning"):
                add(b, "Accuracy includes unverified model knowledge; it is not independently certified.", "warning")
            quotes(b, e.get("evidence", []), g["criterion_ids"])
            for ref in e.get("reference_evidence", []):
                add(b, ref["quote"], "quote")
                add(b, f"Reference {ref['reference_id']} | {ref.get('version', 'Not recorded')} | {ref.get('url', 'No URL recorded')}", "note")

    b = section("7. Consolidated assessment and verification plan")
    if not f6:
        add(b, "F6 is not part of this selected F5 result. Its findings have not been inferred or generated for this export.", "warning")
    else:
        sources = {s["id"]: s for s in f6["source_registry"]}
        def cite(ids):
            for sid in ids:
                s = sources[sid]
                add(b, f"Source {sid} | {label(s['kind'])}", "note")
                add(b, s.get("justification") or f"Coverage: {label(s.get('exploration', 'Not recorded'))}")
                quotes(b, s.get("evidence", []), s.get("criterion_ids", []))
        add(b, f"Suitability: {label(f6['findings']['suitability']['position'])}", "heading")
        add(b, f6["findings"]["suitability"]["rationale"])
        cite(f6["findings"]["suitability"]["source_ids"])
        for key, title in (("strengths", "Strengths"), ("weaknesses", "Observed weaknesses"), ("evidence_gaps", "Evidence gaps"), ("recommended_areas", "Recommended areas to verify")):
            add(b, title, "heading")
            findings = f6["findings"][key]
            if not findings:
                add(b, "No supported finding recorded in this section.")
            for finding in findings:
                add(b, finding["text"])
                add(b, criteria[finding["criterion_id"]], "note")
                cite(finding["source_ids"])

    b = section("8. Complete saved interview transcript" if text_only else "8. Complete chronological interview transcript")
    add(b, prepared["timing_warning"] if text_only else "Every stored segment from the frozen analysis input is included, including unscored conversation and interruptions. Timing reflects the source timeline; overlapping segments are preserved. Audio and video are not embedded.", "note")
    group_by_turn = {}
    for r in f5["results"]:
        for field, role in roles:
            for tid in r["group"][field]:
                group_by_turn.setdefault(tid, []).append(f"{r['group']['id']} / {role}")
    for t in turns:
        add(b, f"[{clock(t['session_start_ms'])}-{clock(t['session_end_ms'])}] {'Candidate' if t['speaker_role'] == 'candidate' else 'Interviewer'} | " + ", ".join(group_by_turn.get(t["id"], ["Unscored context"])), "heading")
        add(b, t["text"])
        add(b, f"Segment {t['id']}" + (f" | Quality flags: {', '.join(t['quality_flags'])}" if t.get("quality_flags") else ""), "note")

    b = section("9. Provenance and limitations")
    for key, value in (("Report schema", "distill-full-assessment-v1"), ("Selected run", meta["run"]["id"]),
        ("F5 run", str(parent.id)), ("Application", meta["candidate"]["application_id"] or "Synthetic; not attached"),
        ("CV assessment", cv["id"] if cv else "Not pinned"), ("Transcript", prepared.get("source_transcript_id") or prepared.get("fixture_id")),
        ("Transcript version", prepared.get("source_version") or prepared.get("fixture_revision")),
        ("Transcript snapshot hash", prepared.get("source_snapshot_sha256")), ("F5 prompt", inputs.get("f5_prompt_version", inputs["prompt_version"])),
        ("F5 rubric", f5.get("rubric_version")), ("F5 model", f5.get("model_version")),
        ("F5 artifact hash", f5.get("artifact_sha256")), ("Selected artifact hash", (f6 or f5).get("artifact_sha256"))):
        add(b, f"{key}: {value if value is not None else 'Not recorded'}", "note")
    if f6:
        for key, value in f6["provenance"].items():
            add(b, f"F6 {label(key)}: {value}", "note")
    recovery = inputs.get("recovery") or f5.get("recovery")
    if recovery:
        add(b, "Bounded evaluation recovery: validated earlier stages were inherited rather than rescored.", "warning")
        for key, value in recovery.items():
            add(b, f"Recovery {label(key)}: {value}", "note")
    for warning in sorted(set(f5.get("warnings", []) + (f6 or {}).get("warnings", []))):
        add(b, label(warning), "warning")
    add(b, "Traceability verifies source references and quotes; it does not establish that every AI interpretation is correct. Review prompting, later corrections, missing evidence and transcription quality before using conclusions. This export contains no new model judgments.")
    coverage = {row["criterion_id"]: row for row in f5.get("coverage", [])}
    matches = {row["requirement_id"]: row for row in (cv or {}).get("matches", [])}
    coverage_rows = []
    for criterion in inputs["criteria"]:
        cid = criterion["id"]
        row = coverage.get(cid)
        refs = sorted(evidence_by_criterion.get(cid, []), key=lambda ref: int(ref[1:]))
        coverage_rows.append([f"{cid}: {criterion['text']} ({criterion.get('priority') or 'Priority not recorded'})",
            label(matches[cid]["decision"]) if cid in matches else "Not recorded",
            label(row["exploration"]) if row else "Not recorded",
            ", ".join(row["group_ids"]) if row and row["group_ids"] else "None recorded",
            ", ".join(refs) if refs else "No cited quote"])
    sections[4]["table"] = {"columns": ["Criterion", "CV screening", "Interview coverage", "Question groups", "Evidence"], "rows": coverage_rows}
    b = section("10. Evidence appendix")
    sections[-1]["id"] = "evidence-appendix"
    add(b, "Numbered references identify exact saved quotes. Interview and CV sources remain distinct. These are frozen assessment sources; current original-file availability is not checked by this export.", "note")
    if not evidence_entries:
        add(b, "No quoted evidence was stored for this assessment. Missing evidence is not a zero score.", "warning")
    for entry in evidence_entries:
        blocks = [{"kind": "note", "text": entry["source"]}, {"kind": "quote", "text": entry["quote"]}]
        # Each reference stays addressable in the browser and readable in exported PDFs.
        b.append({"kind": "heading", "text": f"{entry['id']} | {entry['kind']} evidence", "anchor": entry["id"]})
        b.extend(blocks)
    source_versions = {"criteria": inputs["profile_version"],
        "transcript": prepared.get("source_version") or prepared.get("fixture_revision"),
        "interview_rubric": f5.get("rubric_version"),
        "interview_prompt": inputs.get("f5_prompt_version", inputs.get("prompt_version")),
        "assessment_report": inputs.get("rubric_version") if f6 else None}
    return {"schema": "distill-full-assessment-v1", "title": "Full candidate assessment report", "metadata": {**meta, "source_versions": source_versions}, "sections": sections,
        "filename": f"distill-assessment-{meta['run']['id']}.pdf"}
