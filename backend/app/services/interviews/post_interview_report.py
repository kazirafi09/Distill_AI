"""Project explicitly pinned, completed post-interview results into saved reports."""
from fastapi import HTTPException
from sqlalchemy import select

from app.models.assessments import AssessmentResult, AssessmentFindingReview
from app.services.interviews.analysis import digest
from app.services.interviews.assessment_runs import owned_run
from app.services.interviews.post_interview_analysis import source_warnings


def label(text):
    return str(text).replace("_", " ").capitalize()


async def append(db, document, parent_id, recruiter_id, run_ids, include_evidence):
    if len(run_ids) != len(set(run_ids)) or len(run_ids) > 3:
        raise HTTPException(422, "Select at most one completed result per post-interview feature.")
    pinned, seen = [], set()
    for run_id in run_ids:
        run = await owned_run(db, run_id, recruiter_id)
        saved = await db.get(AssessmentResult, run.id)
        if (run.kind not in {"cv_claim_review", "interview_performance", "interview_quality"} or run.kind in seen or run.parent_run_id != parent_id
                or run.status != "completed" or not saved or saved.payload.get("artifact_sha256") !=
                digest({k: v for k, v in saved.payload.items() if k != "artifact_sha256"})):
            raise HTTPException(409, "Selected post-interview results must be completed, distinct and use this report's exact F5 source.")
        seen.add(run.kind)
        payload = saved.payload
        display_name = {"cv_claim_review": "CV claim review", "interview_performance": "Interview performance", "interview_quality": "Interview quality"}[run.kind]
        blocks = [{"kind": "note", "text": f"Saved {display_name} run {run.id} · {run.created_at.isoformat()} · rubric {run.inputs['rubric_version']} · artifact {payload['artifact_sha256']}"}]

        def text(value, kind="text"):
            blocks.append({"kind": kind, "text": str(value)})

        def evidence(excerpts):
            if include_evidence:
                for excerpt in excerpts:
                    text(f"{excerpt.get('speaker_role', 'CV')} · {excerpt.get('turn_id', excerpt.get('source_id', 'source'))}: {excerpt['quote']}", "quote")

        for warning in dict.fromkeys([*source_warnings(run.inputs.get("prepared", {})), *payload.get("warnings", [])]):
            text(warning, "warning")

        if run.kind == "cv_claim_review":
            title = "CV claim review"
            text(payload["selection_note"], "note")
            text("Flags are prompts for human follow-up, never accusations or automatic rejection.", "warning")
            reviews = list(await db.scalars(select(AssessmentFindingReview).where(AssessmentFindingReview.run_id == run.id)
                .order_by(AssessmentFindingReview.created_at, AssessmentFindingReview.id)))
            for claim in payload["claims"]:
                text(f"{label(claim['status'])}: {claim['claim']}", "heading")
                text(claim["explanation"])
                if claim.get("uncertainty"):
                    text("Evidence uncertainty: " + claim["uncertainty"], "warning")
                text("Context: " + claim["scope_comparison"])
                text("Follow up: " + claim["follow_up"])
                evidence([claim["cv_evidence"], *claim["interview_evidence"]])
                for review in reviews:
                    if review.finding_id == claim["id"]:
                        text(f"Recruiter {label(review.payload['action'])} · {review.created_at.isoformat()}: {review.payload['note'] or 'Reviewed without clarification.'}", "note")
        elif run.kind == "interview_performance":
            title = "Interview performance score"
            summary = payload["summary"]
            overall = summary["overall_score_100"]
            text(f"Overall: {overall}/100 · {label(summary['status'])}" if overall is not None else
                 "Overall score unavailable — insufficient interview evidence", "heading")
            text(f"Assessed dimensions: {summary['assessed_dimensions']}; assessed weight {summary['assessed_weight']}%; missing weight {summary['missing_weight']}%.")
            settings = payload["scoring_settings"]
            policy = settings["policy"]
            text(f"Job scoring revision {settings['revision']} · policy {settings['policy_sha256']} · minimum {policy['minimum_assessed_dimensions']} assessed dimensions; essential: {', '.join(map(label, policy['essential_dimensions'])) or 'None'}.", "note")
            text("Interview performance and CV screening remain separate. Partial coverage is provisional.", "note")
            for name, dimension in payload["dimensions"].items():
                value = "Not assessed" if dimension["score"] is None else f"{dimension['score'] * 25}/100"
                text(f"{label(name)}: {value} · {policy['weights'][name]}% weight · {label(dimension['basis'])}", "heading")
                text(dimension["justification"])
                if dimension.get("uncertainty"):
                    text("Evidence uncertainty: " + dimension["uncertainty"], "warning")
                evidence(dimension["evidence"])
                if include_evidence:
                    for reference in dimension["reference_evidence"]:
                        text(f"Reference {reference['reference_id']} · version {reference['version']}: {reference['quote']}", "quote")
        else:
            title = "Interview process quality"
            text(payload["process_summary"])
            for coverage in payload["coverage"]:
                text(f"{coverage['text']}: {label(coverage['status'])} · groups {', '.join(coverage['group_ids']) or 'None'}.")
            text("Discussion coverage does not establish proficiency.", "note")
            for finding in payload["findings"]:
                text(label(finding["category"]), "heading")
                text(finding["observation"])
                if finding.get("uncertainty"):
                    text("Evidence uncertainty: " + finding["uncertainty"], "warning")
                text("Improve: " + finding["improvement"])
                evidence(finding["evidence"])
            timing = payload["speaking_time"]
            if timing["status"] == "measured":
                text(f"Measured speech segments: candidate {timing['candidate_ms'] / 60000:.1f} minutes; interviewer {timing['recruiter_ms'] / 60000:.1f} minutes.")
                text(timing["note"], "note")
            else:
                text("Speaking time unavailable: " + timing["reason"], "note")
        document["sections"].append({"id": f"post-interview-{run.kind}", "title": title, "blocks": blocks})
        pinned.append({"kind": run.kind, "run_id": str(run.id), "artifact_sha256": payload["artifact_sha256"]})
    document["metadata"]["post_interview_results"] = pinned
    return document
