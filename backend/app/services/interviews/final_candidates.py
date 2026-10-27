"""Score/finding-only comparison and explicit recruiter final selection."""
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.assessments import AssessmentRun, AssessmentResult, FinalCandidateSelection, AssessmentFindingReview
from app.models.cv import Candidate, CandidateApplication
from app.models.interviews import InterviewSession
from app.services.recruitment_access import owned_job, lock_job
from app.services.interviews.analysis import digest
from app.services.interviews.final_score import for_run
from app.services.interviews.consolidation import REPORT_VERSION
from app.services.interviews.assessment_runs import latest_transcript
from app.services.interviews.scoring_settings import snapshot as scoring_snapshot

FEATURES = ('interview_performance', 'cv_claim_review', 'interview_quality')


class SelectedCandidate(BaseModel):
    application_id: UUID
    version: str = Field(pattern=r'^[0-9a-f]{64}$')


class SaveFinalCandidates(BaseModel):
    candidates: list[SelectedCandidate] = Field(max_length=200)
    selection_version: str = Field(pattern=r'^[0-9a-f]{64}$')
    confirmed: Literal[True]


def valid_payload(saved):
    payload = saved.payload if saved else None
    return payload if payload and payload.get('artifact_sha256') == digest(
        {k: v for k, v in payload.items() if k != 'artifact_sha256'}) else None


def project_findings(payload, extensions):
    """Allowlist display fields: never return quotes, transcript or source registry."""
    findings = (payload or {}).get('findings', {})
    performance = extensions.get('interview_performance') or {}
    claims = extensions.get('cv_claim_review') or {}
    quality = extensions.get('interview_quality') or {}
    return {
        'suitability': {key: findings.get('suitability', {}).get(key) for key in ('position', 'rationale')},
        'findings': {key: [item['text'] for item in findings.get(key, [])]
                     for key in ('strengths', 'weaknesses', 'evidence_gaps', 'recommended_areas')},
        'qa_scores': {name: value.get('score_100') for name, value in
                      (payload or {}).get('component_scores', {}).get('interview', {}).items()},
        'performance': {name: {'score': value.get('score') * 25 if value.get('score') is not None else None,
                              'justification': value.get('justification', '')}
                        for name, value in performance.get('dimensions', {}).items()},
        'claim_review': [{key: item.get(key) for key in ('status', 'claim', 'explanation', 'follow_up', 'uncertainty')}
                         for item in claims.get('claims', [])],
        'quality_summary': quality.get('process_summary'),
        'quality_findings': [{key: item.get(key) for key in ('category', 'observation', 'improvement', 'uncertainty')}
                             for item in quality.get('findings', [])],
    }


async def candidate_data(db, job, recruiter_id):
    rows = (await db.execute(select(CandidateApplication, Candidate).join(Candidate).where(
        CandidateApplication.job_description_id == job.id, Candidate.recruiter_id == recruiter_id)
        .order_by(Candidate.full_name, CandidateApplication.id))).all()
    runs = list(await db.scalars(select(AssessmentRun).where(
        AssessmentRun.job_id == job.id, AssessmentRun.recruiter_id == recruiter_id,
        AssessmentRun.synthetic.is_(False), AssessmentRun.application_id.is_not(None))
        .order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc())))
    latest = {}
    for run in runs:
        latest.setdefault((run.application_id, run.kind, run.parent_run_id if run.kind in FEATURES else None), run)
    current_policy = await scoring_snapshot(db, job.id)
    results = []
    for application, person in rows:
        run = latest.get((application.id, 'f6', None))
        parent = latest.get((application.id, 'f5', None))
        payload = valid_payload(await db.get(AssessmentResult, run.id)) if run and run.status == 'completed' else None
        # Completed F6 reports deliberately remain unpublished for recruiter
        # review. Publication permission is not an assessment-readiness flag.
        if payload and (payload.get('schema') != REPORT_VERSION or payload.get('status') != 'completed'
                        or payload.get('synthetic') is not False or payload.get('error_code')
                        or not isinstance(payload.get('findings'), dict)):
            payload = None
        extensions, statuses, extension_ids = {}, {}, {}
        for kind in FEATURES:
            extension = latest.get((application.id, kind, run.parent_run_id)) if run else None
            saved = valid_payload(await db.get(AssessmentResult, extension.id)) if extension and extension.status == 'completed' else None
            if saved and saved.get('provenance', {}).get('f5_artifact_sha256') != (run.inputs.get('f5') or {}).get('artifact_sha256'):
                saved = None
            extensions[kind] = saved
            statuses[kind] = 'completed' if saved else extension.status if extension and extension.status != 'completed' else 'unavailable'
            extension_ids[kind] = str(extension.id) if extension else None
        score = await for_run(db, run, payload) if run else None
        outdated = bool(run and (job.profile_version != run.inputs.get('profile_version')
            or digest(job.screening_profile) != digest(run.inputs.get('profile_snapshot'))
            or not parent or parent.id != run.parent_run_id))
        transcript = await latest_transcript(db, job.id, application.id, recruiter_id)
        prepared = run.inputs.get('prepared', {}) if run else {}
        if run and (not transcript or transcript.status != 'completed' or not transcript.finalized_at
                    or prepared.get('source_transcript_id') != str(transcript.id)
                    or prepared.get('source_version') != transcript.version):
            outdated = True
        policy = (extensions.get('interview_performance') or {}).get('scoring_settings')
        if policy and (policy.get('revision') != current_policy['revision']
                       or policy.get('policy_sha256') != current_policy['policy_sha256']
                       or policy.get('rubric_version') != current_policy['rubric_version']):
            outdated = True
        session = await db.scalar(select(InterviewSession).where(InterviewSession.application_id == application.id,
            InterviewSession.job_description_id == job.id, InterviewSession.recruiter_id == recruiter_id))
        blocker = ('Complete the interview first.' if not session or session.status != 'completed' else
            'Complete the consolidated assessment first.' if not payload else
            'Assessment uses older criteria, interview analysis or scoring settings.' if outdated else
            'Complete interview scoring first.' if not score or score.get('score_100') is None else None)
        item = {'application_id': str(application.id), 'name': person.full_name,
                'run_id': str(run.id) if run else None, 'profile_version': run.inputs.get('profile_version') if run else None,
                'rubric_version': run.inputs.get('rubric_version') if run else None,
                'outdated': outdated, 'comparable': payload is not None, 'eligible': blocker is None, 'blocker': blocker, 'scores': score,
                'feature_statuses': statuses, 'feature_run_ids': extension_ids,
                'scoring_policy': (extensions.get('interview_performance') or {}).get('scoring_settings'),
                **project_findings(payload, extensions)}
        claim_run = extension_ids.get('cv_claim_review')
        if claim_run:
            reviews = list(await db.scalars(select(AssessmentFindingReview).where(
                AssessmentFindingReview.run_id == UUID(claim_run), AssessmentFindingReview.recruiter_id == recruiter_id)
                .order_by(AssessmentFindingReview.created_at, AssessmentFindingReview.id)))
            by_finding = {review.finding_id: review.payload for review in reviews}
            for original, claim in zip((extensions.get('cv_claim_review') or {}).get('claims', []), item['claim_review']):
                review = by_finding.get(original['id'])
                if review:
                    claim.update(review_action=review.get('action'), review_note=review.get('note'))
        item['version'] = digest(item)
        results.append(item)
    return results


def selection_version(row):
    return digest([row.candidates, row.updated_at.isoformat()] if row else None)


async def workspace(job_id, recruiter_id):
    async with SessionFactory() as db:
        job = await owned_job(db, job_id, recruiter_id)
        candidates = await candidate_data(db, job, recruiter_id)
        saved = await db.get(FinalCandidateSelection, job_id)
        current = {item['application_id']: item for item in candidates}
        snapshots = saved.candidates if saved else []
        return {'candidates': candidates, 'selection': snapshots,
                'selection_version': selection_version(saved),
                'updated_at': saved.updated_at.isoformat() if saved else None,
                'selection_outdated': any(item['application_id'] not in current or
                    item['version'] != current[item['application_id']]['version'] for item in snapshots)}


async def save(job_id, recruiter_id, request):
    identifiers = [str(item.application_id) for item in request.candidates]
    if len(identifiers) != len(set(identifiers)):
        raise HTTPException(422, 'Choose each candidate only once.')
    async with SessionFactory() as db:
        await lock_job(db, job_id)
        job = await owned_job(db, job_id, recruiter_id)
        row = await db.get(FinalCandidateSelection, job_id)
        if selection_version(row) != request.selection_version:
            raise HTTPException(409, 'The final selection changed. Reload before confirming again.')
        candidates = {item['application_id']: item for item in await candidate_data(db, job, recruiter_id)}
        chosen = []
        for selection in request.candidates:
            item = candidates.get(str(selection.application_id))
            if not item or not item['eligible'] or item['version'] != selection.version:
                raise HTTPException(409, 'Candidate scores or eligibility changed. Reload and review before confirming.')
            chosen.append(item)
        if row is None:
            row = FinalCandidateSelection(job_id=job_id, recruiter_id=recruiter_id, candidates=chosen)
            db.add(row)
        else:
            row.candidates = chosen
        await db.commit()
    return await workspace(job_id, recruiter_id)
