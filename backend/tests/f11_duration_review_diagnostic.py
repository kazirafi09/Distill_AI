"""Invented-content-only semantic-review diagnostic, not production acceptance."""
import asyncio
import json
import sys

from app.integrations.cv_model import ModelExecution, provider, model_name, context_tokens
from app.services.jd.edit_policy import document_edit_version
from app.services.wind_tunnel import suggestions, storage
from test_wind_tunnel_duration import JD, PROFILE


async def main():
    if len(sys.argv) == 2:
        packet = json.loads(open(sys.argv[1], encoding='utf-8').read())
        packet['evidence'] = {(e['assessment_id'], e['requirement_id']): e for e in packet['evidence']}
        context = suggestions.Context(**packet)
        execution = ModelExecution(provider(), model_name(), context_tokens())
        state = {}
        for attempt in range(1, suggestions.MAX_ATTEMPTS + 1):
            state['attempt'] = attempt
            stage = 'generate'
            try:
                batch = await suggestions.generate_proposal_batch(context, execution, state)
                state['draft'] = batch.model_dump(mode='json')
                print(json.dumps(dict(stage='generated', attempt=attempt, draft=state['draft'])), flush=True)
                proposals, preview = suggestions.reconcile(batch, context)
                assert proposals
                stage = 'review'
                raw = await suggestions.model_call(suggestions.REVIEW_PROMPT, {**context.payload(), 'combined_preview': preview,
                    'candidates': [{'candidate_id': suggestions.candidate_id(p), **p.model_dump(mode='json')} for p in proposals]},
                    suggestions.BatchReview.model_json_schema(), execution)
                print(json.dumps(dict(stage='review', review=raw)), flush=True)
                suggestions.validate_review(raw, proposals)
                print('INVENTED COMPLETED-ASSESSMENT REPLAY PASSED', flush=True)
                return
            except (ValueError, suggestions.HTTPException) as exc:
                suggestions.failure(state, exc, stage)
                print(json.dumps(dict(stage='failure', attempts=state['attempts'])), flush=True)
                if state['status'] != 'repair':
                    raise
        raise AssertionError('No validated edits within production repair budget')
    snapshot = dict(content=JD, screening_profile=PROFILE, profile_version=1)
    context = suggestions.Context(snapshot, document_edit_version(storage.JobSnapshot(**snapshot)), 'invented-control',
        {'coverage': {'expected_assessments': 3, 'completed_assessments': 3, 'complete_cohorts': 1},
         'weight_sensitivity': {'tested_vectors': []}}, {}, {}, {})
    item = suggestions.GroundedProposal.model_validate({
        'basis': 'jd_quality', 'proposal': {'title': 'Refine PostgreSQL requirement phrasing',
        'reason': 'Improve the phrasing of the PostgreSQL requirement for clarity while strictly preserving existing technical expectations and recruiter intent.',
        'patches': [{'kind': 'description', 'expected': 'PostgreSQL schema design and query optimization.',
                     'proposed': 'Experience with PostgreSQL schema design and query optimization.'}]},
        'source_references': [{'kind': 'description', 'quote': 'PostgreSQL schema design and query optimization.'}]})
    proposals, preview = suggestions.reconcile(suggestions.ProposalBatch(suggestions=[item]), context)
    schema = suggestions.BatchReview.model_json_schema()
    # Explanations are requested only in this invented replay to understand a
    # recorded false flag. Production contracts/passing decisions are untouched.
    schema['$defs']['CandidateReview']['properties']['explanation'] = {'type': 'string'}
    schema['$defs']['CandidateReview']['required'].append('explanation')
    raw = await suggestions.model_call(suggestions.REVIEW_PROMPT,
        {**context.payload(), 'combined_preview': preview,
         'candidates': [{'candidate_id': suggestions.candidate_id(p), **p.model_dump(mode='json')} for p in proposals]},
        schema, ModelExecution(provider(), model_name(), context_tokens()))
    print(json.dumps(raw), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
