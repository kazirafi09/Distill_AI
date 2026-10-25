"""Current target-ID/retained-review regressions; no model or real-job calls."""
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from app.services.jd.edit_policy import document_edit_version
from app.services.wind_tunnel import suggestions as s


def context(content=None):
    profile = dict(criteria=[dict(id='docker', text='Docker and CI/CD workflows.', dimension='technical_skills',
        priority='Preferred', source_quote='Docker and CI/CD workflows.', priority_quote='Preferred: Docker and CI/CD workflows.')],
        addressed_dimensions=['technical_skills', 'work_experience', 'qualifications', 'additional_criteria'],
        dimension_weights=dict(technical_skills=100, work_experience=0, qualifications=0, additional_criteria=0),
        weights_confirmed=True, weights_confirmation_quote='Technical skills 100%',
        minimum_years_experience=None, fresher_eligibility=None, google_form_url=None)
    snapshot = dict(content=content or '# Developer\n\nBuild APIs.\nDesign schemas.\nWrite tests.', screening_profile=profile, profile_version=1)
    versions = dict(generation=dict(persona_keys=['strong_all_rounder', 'keyword_stuffer'], persona_count=2),
                    suggestions=dict(execution=dict(provider='gemini', model='fixture')))
    return s.Context(snapshot, document_edit_version(s.storage.JobSnapshot(**snapshot)), 'fixture',
        dict(coverage=dict(expected_assessments=2, completed_assessments=2, complete_cohorts=1),
             weight_sensitivity=dict(tested_vectors=[])), {}, {}, versions)


def generated(ctx, old, new):
    target = next(t for t in s.editable_targets(ctx) if t['current_value'] == old)
    return dict(basis='jd_quality', finding_ids=[], proposal=dict(title='Clarify ' + old, reason='Clarify the existing wording.',
        changes=[dict(target_id=target['target_id'], replacement=new)]))


def batch(ctx):
    return dict(suggestion=generated(ctx, 'Build APIs.', 'Develop APIs.'), additional_suggestions=[
        generated(ctx, 'Design schemas.', 'Design database schemas.'), generated(ctx, 'Write tests.', 'Write automated tests.')])


def verdict(payload, rejected=()):
    return dict(candidates=[dict(candidate_id=p['candidate_id'], evidence_supported=i not in rejected,
        preserves_intent=True, prose_profile_consistent=True, claims_supported=True, atomic_changes_grouped=True,
        rejection_reasons=['unsupported_source'] if i in rejected else []) for i, p in enumerate(payload['candidates'])])


class TargetResolverTests(unittest.TestCase):
    def test_resolver_derives_description_reference_and_old_value(self):
        ctx = context()
        result = s.resolve_generated_proposals(s.GeneratedEditResponse.model_validate(batch(ctx)), ctx)
        for item in result.suggestions:
            patch_value = item.proposal.patches[0]
            self.assertEqual(patch_value.kind, 'description')
            self.assertEqual(item.source_references[0].kind, 'description')
            self.assertEqual(item.source_references[0].quote, patch_value.expected)
            self.assertEqual(ctx.payload()['public_narrative'][patch_value.start:patch_value.end], patch_value.expected)
        proposals, preview = s.reconcile(result, ctx)
        self.assertEqual(len(proposals), 3)
        self.assertIn('Develop APIs.', preview['content'])

    def test_unknown_target_and_old_model_quote_fields_are_rejected_safely(self):
        ctx = context()
        value = batch(ctx)
        value['suggestion']['proposal']['changes'][0]['target_id'] = 'private text is not an ID'
        with self.assertRaises(s.InvalidModelOutput) as caught:
            s.resolve_generated_proposals(s.GeneratedEditResponse.model_validate(value), ctx)
        self.assertNotIn('private text', str(caught.exception.details))
        self.assertEqual(caught.exception.details[0]['issue'], 'unknown_target')
        value = batch(ctx); value['suggestion']['source_references'] = [{'quote': 'private quote'}]
        with self.assertRaises(s.InvalidModelOutput) as caught:
            s.validate_output(s.GeneratedEditResponse, value)
        self.assertNotIn('private quote', str(caught.exception.details))

    def test_repeated_passages_have_distinct_targets_and_replace_exact_occurrences(self):
        ctx = context('# Developer\n\nSame wording.\n\nSame wording.')
        targets = [t for t in s.editable_targets(ctx) if t['current_value'] == 'Same wording.']
        self.assertEqual(len({t['target_id'] for t in targets}), 2)
        raw = dict(suggestion=dict(basis='jd_quality', proposal=dict(title='Clarify both passages', reason='Fixture.',
            changes=[dict(target_id=t['target_id'], replacement=f'Passage {i}.') for i, t in enumerate(targets, 1)])))
        _, preview = s.reconcile(s.resolve_generated_proposals(s.GeneratedEditResponse.model_validate(raw), ctx), ctx)
        self.assertIn('Passage 1.\n\nPassage 2.', preview['content'])
        self.assertEqual(ctx.snapshot['content'].count('Same wording.'), 2)

    def test_stale_source_and_noop_are_still_rejected(self):
        ctx = context(); value = batch(ctx)
        ctx_stale = s.Context(ctx.snapshot, '0' * 64, ctx.evaluation_id, ctx.report, {}, {}, ctx.versions)
        with self.assertRaises(HTTPException) as caught:
            s.reconcile(s.resolve_generated_proposals(s.GeneratedEditResponse.model_validate(batch(ctx_stale)), ctx_stale), ctx_stale)
        self.assertEqual(caught.exception.status_code, 409)
        value['additional_suggestions'][0]['proposal']['changes'][0]['replacement'] = 'Design schemas.'
        normalized = s.resolve_generated_proposals(s.GeneratedEditResponse.model_validate(value), ctx)
        admitted, details = s.reconcile_candidates(normalized, ctx, [])
        self.assertEqual(len(admitted.suggestions), 2)
        self.assertEqual(details[0]['candidate_index'], 1)
        with self.assertRaises(HTTPException):
            s.reconcile(s.ProposalBatch(suggestions=[normalized.suggestions[1]]), ctx)

    def test_finding_ids_resolve_to_their_actual_evidence_pairs(self):
        ctx = context(); aid = str(uuid4())
        ctx.findings['finding-1'] = dict(references=[dict(assessment_id=aid, requirement_id='docker')])
        value = generated(ctx, 'Docker and CI/CD workflows.', 'Use Docker containerization and CI/CD workflows.')
        value.update(basis='diagnostic', finding_ids=['finding-1'])
        result = s.resolve_generated_proposals(s.GeneratedEditResponse.model_validate(dict(suggestion=value)), ctx)
        self.assertEqual(result.suggestions[0].evidence_references[0].model_dump(mode='json'), dict(assessment_id=aid, requirement_id='docker'))
        value['finding_ids'] = ['foreign-finding']
        with self.assertRaises(s.InvalidModelOutput):
            s.resolve_generated_proposals(s.GeneratedEditResponse.model_validate(dict(suggestion=value)), ctx)


class RetainedRepairTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, final_rejection=False):
        ctx = context()
        run = NS(resources={'analysis': {'token': 'writer', 'evaluation_id': 'fixture'}})
        db = NS(commit=AsyncMock())
        @asynccontextmanager
        async def factory():
            yield db
        calls, review_calls = [], []
        async def model(prompt, payload, schema, execution):
            if prompt == s.PROMPT:
                calls.append(deepcopy(payload))
                if len(calls) == 1:
                    return batch(ctx)
                self.assertEqual(payload['requested_new_suggestions'], 1)
                self.assertEqual(len(payload['retained_suggestions']), 2)
                retained_ranges = {(p['start'], p['end']) for item in payload['retained_suggestions'] for p in item['proposal']['patches']}
                self.assertTrue(all((t.get('start'), t.get('end')) not in retained_ranges for t in payload['editable_targets'] if t['kind'] == 'description'))
                return dict(suggestion=generated(ctx, 'Design schemas.', 'Develop database schemas.'), additional_suggestions=[])
            review_calls.append(deepcopy(payload))
            rejected = (1,) if len(review_calls) == 1 else (2,) if final_rejection and len(review_calls) == 2 else ()
            return verdict(payload, rejected)
        with patch.object(s, 'load_context', new=AsyncMock(return_value=ctx)), \
             patch.object(s.storage, 'owned_analysis', new=AsyncMock(return_value=run)), \
             patch.object(s, 'model_version', new=AsyncMock(return_value={})), \
             patch.object(s, 'model_call', new=AsyncMock(side_effect=model)):
            await s.draft_suggestions(uuid4(), uuid4(), 'writer', root=None, session_factory=factory)
            state = await s.review_suggestions(uuid4(), uuid4(), 'writer', root=None, session_factory=factory)
            self.assertEqual(state['status'], 'repair')
            retained = deepcopy(state['retained_proposals'])
            self.assertEqual(len(retained), 2)
            await s.draft_suggestions(uuid4(), uuid4(), 'writer', root=None, session_factory=factory)
            state = await s.review_suggestions(uuid4(), uuid4(), 'writer', root=None, session_factory=factory)
        self.assertEqual(state['status'], 'validated')
        self.assertIsNone(state['error_code'])
        self.assertEqual(state['attempt'], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state['proposals'][:2], retained)
        self.assertEqual(len(state['proposals']), 2 if final_rejection else 3)
        self.assertEqual(len(review_calls), 3 if final_rejection else 2)
        if final_rejection:
            self.assertEqual(len(review_calls[-1]['candidates']), 2)
            self.assertIn('Only 2 of 3', state['count_note'])
        s.validate_review(state['review'], [s.GroundedProposal.model_validate(p) for p in state['proposals']])

    async def test_two_accepted_proposals_survive_repair_and_three_are_finally_reviewed(self):
        await self.exercise()

    async def test_final_accepted_subset_is_re_reviewed_as_its_exact_union(self):
        await self.exercise(final_rejection=True)


if __name__ == '__main__':
    unittest.main()
