"""Source-grounding boundaries and durable progress regression checks."""
import unittest
import os
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import patch, AsyncMock

from app.services.jd.suggestions import validate_quality_sources
from app.services.wind_tunnel.progress import progress_for
from app.services.wind_tunnel.suggestions import GroundedProposal, ProposalBatch, reconcile, Context, generate_proposal_batch, PROMPT, validate_review, candidate_id, failure
from app.services.wind_tunnel import suggestions


class SourceGroundingTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = {'content': 'Use Python to build APIs.', 'screening_profile': {'criteria': [
            {'id': 'c1', 'text': 'Python experience', 'priority': 'Must have'}]}}
        self.proposal = NS(patches=[NS(kind='criterion', field='text', criterion_id='c1')])
        self.refs = [{'kind': 'criterion', 'criterion_id': 'c1', 'quote': 'Python experience'}]

    def test_exact_owned_source_required(self):
        validate_quality_sources(self.snapshot, self.proposal, self.refs)
        for refs in ([], [dict(self.refs[0], quote='Java experience')], [dict(self.refs[0], criterion_id='foreign')]):
            with self.assertRaises(ValueError):
                validate_quality_sources(self.snapshot, self.proposal, refs)

    def test_quality_cannot_change_policy(self):
        for kind, field in [('weights', None), ('metadata', None), ('criterion', 'priority')]:
            with self.assertRaises(ValueError):
                validate_quality_sources(self.snapshot, NS(patches=[NS(kind=kind, field=field)]), self.refs)

    def test_description_quote_must_ground_changed_passage(self):
        p = NS(patches=[NS(kind='description', expected='build APIs')])
        with self.assertRaises(ValueError):
            validate_quality_sources(self.snapshot, p, [{'kind': 'description', 'quote': 'Use Python'}])

    def test_quality_reconciles_without_synthetic_findings_but_diagnostic_cannot(self):
        item = GroundedProposal.model_validate({'basis': 'jd_quality', 'source_references': self.refs,
            'proposal': {'title': 'Clarify wording', 'reason': 'Make the wording more direct.',
                'patches': [{'kind': 'criterion', 'criterion_id': 'c1', 'field': 'text',
                            'expected': 'Python experience', 'proposed': 'Experience using Python'}]}})
        snapshot = {**self.snapshot, 'profile_version': 1}
        context = Context(snapshot, 'version', 'eval', {}, {}, {}, {})
        with patch('app.services.wind_tunnel.suggestions.preview_suggestion', return_value=NS(model_dump=lambda **kw: {})):
            proposals, _ = reconcile(ProposalBatch(suggestions=[item]), context)
            self.assertEqual(len(proposals), 1)
            with self.assertRaisesRegex(ValueError, 'TARGET_UNGROUNDED'):
                reconcile(ProposalBatch(suggestions=[item.model_copy(update={'basis': 'diagnostic'})]), context)


class ModelContractTests(unittest.TestCase):
    def test_provider_failure_retains_only_fixed_public_cause_codes(self):
        state = failure({'attempt': 1}, ValueError('MODEL_REQUEST_INVALID'), 'generate')
        self.assertEqual(state['error_code'], 'SUGGESTION_PROVIDER_FAILED')
        self.assertEqual(state['attempts'][0]['cause_code'], 'MODEL_REQUEST_INVALID')
        self.assertEqual(state['status'], 'failed')
        secret = 'MODEL_private response with secret and CV prose'
        state = failure({'attempt': 1}, ValueError(secret), 'generate')
        self.assertNotIn(secret, str(state))
        self.assertNotIn('cause_code', state['attempts'][0])

    def test_provider_schema_preserves_all_edit_variants_and_kind_values(self):
        from app.integrations.cv_model import gemini_schema, ollama_structural_schema
        for convert in (gemini_schema, ollama_structural_schema):
            schema = convert(ProposalBatch.model_json_schema())
            patches = schema['properties']['suggestions']['items']['properties']['proposal']['properties']['patches']['items']
            self.assertEqual(len(patches['anyOf']), 4)
            variants = {item['properties']['kind']['enum'][0]: item for item in patches['anyOf']}
            self.assertEqual(set(variants), {'description', 'criterion', 'weights', 'metadata'})
            for variant in variants.values():
                self.assertIn('expected', variant['required'])
                self.assertIn('proposed', variant['required'])
            self.assertIn('criterion_id', variants['criterion']['required'])
        generated = gemini_schema(suggestions.GeneratedEditResponse.model_json_schema())
        self.assertIn('suggestion', generated['required'])
        proposal = generated['properties']['suggestion']['properties']['proposal']
        self.assertIn('changes', proposal['required'])
        change = proposal['properties']['changes']['items']
        self.assertEqual(set(change['required']), {'target_id', 'replacement'})
        self.assertNotIn('expected', change['properties'])
        self.assertNotIn('source_references', generated['properties']['suggestion']['properties'])

    def test_contract_feedback_identifies_fields_without_rejected_content(self):
        secret = 'private generated text that must not be stored'
        with self.assertRaises(suggestions.InvalidModelOutput) as caught:
            suggestions.validate_output(ProposalBatch, {'suggestions': [{'proposal': {'title': secret, 'reason': 'Reason',
                'patches': [{'kind': 'description', 'expected': secret}]}}]})
        state = failure({'attempt': 1}, caught.exception, 'generate')
        self.assertIn('proposed', str(state['validation_details']))
        self.assertNotIn(secret, str(state))
        self.assertEqual(state['status'], 'repair')


class HistoryRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_suggestion_only_config_change_requests_new_batch(self):
        from copy import deepcopy
        from dataclasses import asdict
        from app.integrations.cv_model import ModelExecution
        from app.services.wind_tunnel import generation, review
        from app.services.jd.edit_policy import document_edit_version
        job = NS(content='Invented role.', screening_profile={}, profile_version=1)
        run_id = uuid4()
        stamp = datetime.now(timezone.utc)
        task = NS(id=uuid4(), run_id=run_id, status='failed', source_version=document_edit_version(job), trigger='more',
                  attempts=1, error_code='SUGGESTION_SEMANTIC_REVIEW_FAILED', created_at=stamp, updated_at=stamp)
        versions = {'generation': {'generator': generation.GENERATOR_VERSION, 'validator': generation.VALIDATOR_VERSION,
                    'renderer': generation.RENDERER_VERSION, 'execution': asdict(ModelExecution('gemini', 'fixture'))}}
        versions['suggestions'] = suggestions.config_for(versions)
        for changed in (None, 'version', 'prompt_hash', 'review_prompt_hash'):
            with self.subTest(changed=changed):
                config = deepcopy(versions)
                if changed:
                    config['suggestions'][changed] = 'previous'
                run = NS(id=run_id, versions=config, resources={})
                db = NS(scalars=AsyncMock(side_effect=[NS(all=lambda: [task]), NS(all=lambda: [run]),
                    NS(all=lambda: []), NS(all=lambda: []), NS(all=lambda: [])]))
                with patch.object(review, 'owned_job', new=AsyncMock(return_value=job)), \
                     patch.object(review, 'posting_status', new=AsyncMock(return_value=None)), \
                     patch.object(review, 'progress_for', return_value={}):
                    result = await review.history(db, uuid4(), uuid4())
                self.assertEqual(result['batches'][0]['needs_new_batch'], changed is not None)


class ProgressTests(unittest.TestCase):
    def test_live_counts_before_evaluation_and_expired_lease(self):
        now = datetime.now(timezone.utc)
        task = NS(status='running', updated_at=now, lease_until=now + timedelta(seconds=60))
        run = NS(resources={'analysis': {'stage': 'assess_cohorts'}}, versions={'assessment_repetitions': 1},
                 snapshot={'screening_profile': {'criteria': [1, 2]}}, updated_at=now)
        rows = [NS(payload={'status': 'completed', 'matches': [1, 2]}, updated_at=now),
                NS(payload={'status': 'running', 'matches': [1]}, updated_at=now)]
        value = progress_for(task, run, [1, 2, 3], rows)
        self.assertEqual((value['assessments_completed'], value['criteria_completed'], value['criteria_total']), (1, 3, 6))
        self.assertTrue(value['worker_connected'])
        task.lease_until = now - timedelta(seconds=1)
        self.assertFalse(progress_for(task, run, [], rows)['worker_connected'])
        task.status = 'limited'
        self.assertLess(progress_for(task, run, [], rows)['step'], 6)
        task.status = 'completed'
        self.assertEqual(progress_for(task, run, [], rows)['step'], 6)

    def test_waiting_before_run_exists(self):
        task = NS(status='queued', updated_at=datetime.now(timezone.utc), lease_until=None)
        self.assertEqual(progress_for(task, None, [], [])['label'], 'Waiting for the worker')


class ExactEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_requires_an_edit_and_does_not_offer_questions(self):
        from app.services.jd.edit_policy import document_edit_version
        snapshot = {'content': '# Developer\nBuild APIs', 'screening_profile': {'criteria': []}, 'profile_version': 1}
        context = Context(snapshot, document_edit_version(suggestions.storage.JobSnapshot(**snapshot)), 'fixture',
                          {'coverage': {'expected_assessments': 2, 'completed_assessments': 2}, 'weight_sensitivity': {'tested_vectors': []}}, {}, {}, {})
        target = next(t for t in suggestions.editable_targets(context) if t['current_value'] == 'Build APIs')
        generated = {'basis': 'jd_quality', 'proposal': {'title': 'Clarify wording', 'reason': 'Clarify the existing responsibility.',
                     'changes': [{'target_id': target['target_id'], 'replacement': 'Develop APIs'}]}}
        with patch.object(suggestions, 'model_call', new=AsyncMock(return_value={'suggestion': generated})) as call:
            result = await generate_proposal_batch(context, None, {})
            self.assertEqual(len(result.suggestions), 1)
            call.assert_awaited_once()
            self.assertIn('evaluation', call.call_args.args[1])
            self.assertNotIn('guidance', call.call_args.args[2]['properties'])
            self.assertIn('suggestion', call.call_args.args[2]['required'])
            self.assertEqual(call.call_args.args[1]['requested_new_suggestions'], 3)
            self.assertEqual(result.suggestions[0].source_references[0].quote, 'Build APIs')
            self.assertEqual(result.suggestions[0].proposal.patches[0].expected, 'Build APIs')
        for raw in ({'suggestions': []}, {'suggestions': [], 'guidance': [{'question': 'Which CI tool?'}]}):
            with self.assertRaises(suggestions.InvalidModelOutput):
                suggestions.validate_output(ProposalBatch, raw)
        self.assertEqual(failure({'attempt': 1}, suggestions.InvalidModelOutput([]), 'generate')['status'], 'repair')
        self.assertEqual(failure({'attempt': 2}, suggestions.InvalidModelOutput([]), 'generate')['status'], 'failed')

    async def test_reviewer_must_cover_each_exact_proposal(self):
        item = GroundedProposal.model_validate({'basis': 'jd_quality', 'proposal': {'title': 'Clarify wording', 'reason': 'Clarify the responsibility.',
            'patches': [{'kind': 'description', 'expected': 'Build APIs', 'proposed': 'Develop APIs'}]},
            'source_references': [{'kind': 'description', 'quote': 'Build APIs'}]})
        flags = {'candidate_id': candidate_id(item), 'evidence_supported': True, 'preserves_intent': True,
                 'prose_profile_consistent': True, 'claims_supported': True, 'atomic_changes_grouped': True}
        validate_review({'candidates': [flags]}, [item])
        with self.assertRaises(ValueError):
            validate_review({'candidates': []}, [item])
        with self.assertRaises(ValueError):
            validate_review({'candidates': [{**flags, 'preserves_intent': False}]}, [item])
        with self.assertRaises(suggestions.InvalidModelOutput) as caught:
            validate_review({'candidates': [{**flags, 'claims_supported': False, 'rejection_reasons': ['adds_evidence_burden']}]}, [item])
        self.assertEqual(caught.exception.details[0]['rejection_reasons'], ['adds_evidence_burden'])
        # A reason cannot be ignored just because the Boolean flags contradict it.
        with self.assertRaises(suggestions.InvalidModelOutput):
            validate_review({'candidates': [{**flags, 'rejection_reasons': ['adds_requirement']}]}, [item])
        secret = 'private rejected model prose'
        with self.assertRaises(suggestions.InvalidModelOutput) as caught:
            validate_review({'candidates': [{**flags, 'rejection_reasons': [secret]}]}, [item])
        self.assertNotIn(secret, str(caught.exception.details))

    async def test_deduplication_preserves_prior_exact_edits(self):
        item = GroundedProposal.model_validate({'basis': 'jd_quality', 'proposal': {'title': 'Clarify wording', 'reason': 'Clarify the responsibility.',
            'patches': [{'kind': 'description', 'expected': 'Build APIs', 'proposed': 'Develop APIs'}]},
            'source_references': [{'kind': 'description', 'quote': 'Build APIs'}]})
        context = Context({'content': 'Build APIs', 'screening_profile': {'criteria': []}}, 'v', 'e', {}, {}, {}, {},
                          (item.proposal.model_dump(mode='json')['patches'],))
        self.assertEqual(reconcile(ProposalBatch(suggestions=[item]), context), ([], None))
        self.assertEqual(failure({'attempt': 1}, ValueError('SUGGESTION_NO_NEW_EDITS'), 'review')['status'], 'limited')


@unittest.skipUnless(os.environ.get('RUN_F11_DB_TESTS') == '1', 'Opt in to disposable-schema SQL acceptance')
class ApplyAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.schema import CreateSchema
        from sqlalchemy.pool import NullPool
        from app.core.config import get_settings
        from app.db.base import Base
        from app.models.user import User
        from app.models.jd import JDConversation, JobDescription, DiscordJobPost
        from app.models.assistant import AssistantConversation
        from app.models.wind_tunnel import WindTunnelRun, WindTunnelSuggestion
        self.engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        self.schema = 'f11_acceptance_' + uuid4().hex
        async with self.engine.begin() as conn:
            await conn.execute(CreateSchema(self.schema))
        self.scoped = self.engine.execution_options(schema_translate_map={None: self.schema})
        tables = [c.__table__ for c in (User, JDConversation, JobDescription, DiscordJobPost, AssistantConversation, WindTunnelRun, WindTunnelSuggestion)]
        async with self.scoped.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
        self.factory = async_sessionmaker(self.scoped, expire_on_commit=False)

    async def asyncTearDown(self):
        from sqlalchemy.schema import DropSchema
        assert self.schema.startswith('f11_acceptance_') and len(self.schema) == len('f11_acceptance_') + 32
        try:
            async with self.engine.begin() as conn:
                await conn.execute(DropSchema(self.schema, cascade=True))
        finally:
            await self.engine.dispose()

    async def test_exact_proposals_review_preview_apply_and_atomic_stale_markers(self):
        from app.models.user import User
        from app.models.jd import JDConversation, JobDescription, DiscordJobPost
        from app.models.assistant import AssistantConversation
        from app.models.wind_tunnel import WindTunnelRun, WindTunnelSuggestion
        from app.services.wind_tunnel import review
        from app.services.jd.edit_policy import document_edit_version
        from fastapi import HTTPException
        from sqlalchemy import select
        profile = {'criteria': [{'id': 'docker', 'text': 'Docker and CI/CD experience.', 'dimension': 'technical_skills',
            'priority': 'Preferred', 'source': 'recruiter', 'source_quote': 'Docker and CI/CD experience.',
            'priority_quote': 'Preferred: Docker and CI/CD experience.', 'priority_source': 'recruiter'}],
            'addressed_dimensions': ['technical_skills', 'work_experience', 'qualifications', 'additional_criteria'],
            'dimension_weights': {'technical_skills': 100, 'work_experience': 0, 'qualifications': 0, 'additional_criteria': 0},
            'weights_confirmed': True, 'weights_confirmation_quote': 'Confirmed technical skills 100%',
            'minimum_years_experience': None, 'fresher_eligibility': None, 'google_form_url': None}
        content = '# Backend Developer\n\n## Responsibilities\nBuild REST APIs.\n\n## Preferred\nDocker and CI/CD experience.'
        async with self.factory() as db:
            user = User(email='synthetic@example.invalid', password_hash='unused', role='recruiter')
            db.add(user); await db.flush()
            conversation = JDConversation(recruiter_id=user.id)
            db.add(conversation); await db.flush()
            db.add(AssistantConversation(user_id=user.id, jd_conversation_id=conversation.id))
            job = JobDescription(conversation_id=conversation.id, title='Backend Developer', content=content,
                requirements='Synthetic fixture', screening_profile=profile, profile_version=1)
            db.add(job); await db.flush()
            version = document_edit_version(job)
            run = WindTunnelRun(owner_id=user.id, job_id=job.id, request_key='fixture', source_version=version,
                analysis_version='fixture', trigger='more', snapshot={'content':content, 'screening_profile':profile, 'profile_version':1},
                versions={}, status='running', resources={'analysis': {'token': 'fixture', 'evaluation_id': 'fixture'}}, findings=[])
            db.add(run); await db.flush()
            draft = ProposalBatch.model_validate({'suggestions': [
                {'basis': 'jd_quality', 'proposal': {'title': 'Clarify deployment experience', 'reason': 'Express the existing tools as experience using them.',
                 'patches': [{'kind': 'criterion', 'criterion_id': 'docker', 'field': 'text', 'expected': 'Docker and CI/CD experience.', 'proposed': 'Experience using Docker and working with CI/CD pipelines.'},
                             {'kind': 'description', 'expected': 'Docker and CI/CD experience.', 'proposed': 'Experience using Docker and working with CI/CD pipelines.'}]},
                 'source_references': [{'kind':'criterion','criterion_id':'docker','quote':'Docker and CI/CD experience.'}, {'kind':'description','quote':'Docker and CI/CD experience.'}]},
                {'basis':'jd_quality','proposal':{'title':'Clarify API responsibility','reason':'Make the existing action more explicit.',
                 'patches':[{'kind':'description','expected':'Build REST APIs.','proposed':'Develop REST APIs.'}]},
                 'source_references':[{'kind':'description','quote':'Build REST APIs.'}]}
            ]})
            run.resources = {**run.resources, 'suggestions': {'status':'drafted','attempt':suggestions.MAX_ATTEMPTS,'version':suggestions.VERSION,'evaluation_id':'fixture','draft':draft.model_dump(mode='json')}}
            context = Context(run.snapshot, version, 'fixture', {'coverage':{'expected_assessments':2},'weight_sensitivity':{'tested_vectors':[]}}, {}, {},
                {'generation':{'persona_keys':['strong_all_rounder','keyword_stuffer'],'persona_count':2}, 'suggestions':{'execution':{'provider':'gemini','model':'fixture'}}})
            await db.commit()
            run_id, job_id, owner_id = run.id, job.id, user.id

        async def accepted(prompt, payload, schema, execution):
            return {'candidates':[{'candidate_id':c['candidate_id'],'evidence_supported':True,'preserves_intent':True,
                'prose_profile_consistent':True,'claims_supported':True,'atomic_changes_grouped':True} for c in payload['candidates']]}
        with patch.object(suggestions,'load_context',new=AsyncMock(return_value=context)), \
             patch.object(suggestions,'model_version',new=AsyncMock(return_value={})), \
             patch.object(suggestions,'model_call',new=AsyncMock(side_effect=accepted)):
            state = await suggestions.review_suggestions(run_id,owner_id,'fixture',root=None,session_factory=self.factory)
            self.assertEqual(state['status'],'validated')
            saved = await suggestions.persist_suggestions(run_id,owner_id,'fixture',root=None,session_factory=self.factory)
            self.assertEqual(len(saved['proposal_ids']),2)
        async with self.factory() as db:
            rows=(await db.scalars(select(WindTunnelSuggestion).where(WindTunnelSuggestion.run_id==run_id).order_by(WindTunnelSuggestion.created_at))).all()
            linked=next(r for r in rows if len(r.payload['proposal']['patches'])==2)
            preview,_=await review.preview(db,job_id,owner_id,[linked.id],version)
            self.assertIn('CI/CD pipelines',preview['edit']['content'])
            self.assertEqual((await db.get(JobDescription,job_id)).content,content)  # Preview/cancel saves nothing.
            with self.assertRaises(HTTPException) as missing_confirmation:
                await review.apply(db,None,job_id,owner_id,[linked.id],version,preview['review_token'],False)
            self.assertEqual(missing_confirmation.exception.status_code,422)
            with self.assertRaises(HTTPException) as foreign:
                await review.preview(db,job_id,uuid4(),[linked.id],version)
            self.assertEqual(foreign.exception.status_code,404)
            with self.assertRaises(HTTPException) as stale:
                await review.preview(db,job_id,owner_id,[linked.id],'0'*64)
            self.assertEqual(stale.exception.status_code,409)
            graph=NS(get_state=lambda config:NS(values={}))
            with patch('app.services.jd.editing.load_jd_conversation',new=AsyncMock(return_value={'status':'approved'})):
                await review.apply(db,graph,job_id,owner_id,[linked.id],version,preview['review_token'],True)
            job=await db.get(JobDescription,job_id,populate_existing=True)
            self.assertIn('Experience using Docker and working with CI/CD pipelines.',job.content)
            self.assertEqual(job.screening_profile['criteria'][0]['text'],'Experience using Docker and working with CI/CD pipelines.')
            self.assertEqual(job.profile_version,2)
            await db.refresh(linked)
            self.assertEqual(linked.status,'applied')
            other=next(r for r in rows if r.id!=linked.id); await db.refresh(other)
            self.assertEqual(other.status,'stale')
            with self.assertRaises(HTTPException) as repeat:
                await review.apply(db,graph,job_id,owner_id,[linked.id],version,preview['review_token'],True)
            self.assertEqual(repeat.exception.status_code,409)
            db.add(DiscordJobPost(job_description_id=job_id,status='posted')); await db.commit()
            with self.assertRaises(HTTPException) as locked:
                await review.preview(db,job_id,owner_id,[other.id],document_edit_version(job))
            self.assertEqual(locked.exception.status_code,409)


    async def test_multiple_apply_guards_and_rollback_after_applied_markers(self):
        from copy import deepcopy
        from fastapi import HTTPException
        from sqlalchemy import select
        from app.models.user import User
        from app.models.jd import JDConversation, JobDescription, DiscordJobPost
        from app.models.assistant import AssistantConversation
        from app.models.wind_tunnel import WindTunnelRun, WindTunnelSuggestion
        from app.services.wind_tunnel import review
        from app.services.jd.edit_policy import document_edit_version
        content = '# Backend Developer\n\nBuild REST APIs.\nMaintain services.\n\nDocker and CI/CD experience.'
        profile = dict(criteria=[dict(id='docker', text='Docker and CI/CD experience.', dimension='technical_skills', priority='Preferred',
            source_quote='Docker and CI/CD experience.', priority_quote='Preferred: Docker and CI/CD experience.')],
            addressed_dimensions=['technical_skills','work_experience','qualifications','additional_criteria'],
            dimension_weights=dict(technical_skills=100,work_experience=0,qualifications=0,additional_criteria=0),
            weights_confirmed=True,weights_confirmation_quote='Technical skills 100%', minimum_years_experience=None,fresher_eligibility=None,google_form_url=None)
        async with self.factory() as db:
            user = User(email='multi@example.invalid', password_hash='unused', role='recruiter')
            db.add(user); await db.flush()
            conversation = JDConversation(recruiter_id=user.id); db.add(conversation); await db.flush()
            db.add(AssistantConversation(user_id=user.id, jd_conversation_id=conversation.id))
            job = JobDescription(conversation_id=conversation.id,title='Backend Developer',content=content,requirements='Invented.',screening_profile=profile,profile_version=1)
            db.add(job); await db.flush()
            version = document_edit_version(job)
            def run(key):
                return WindTunnelRun(owner_id=user.id,job_id=job.id,request_key=key,source_version=version,analysis_version='fixture',trigger='more',
                    snapshot=dict(content=content,screening_profile=deepcopy(profile),profile_version=1),versions={},status='completed',resources={},findings=[])
            first, other = run('multi'), run('foreign-batch'); db.add_all([first,other]); await db.flush()
            def row(run_id, title, patches):
                proposal = suggestions.SuggestionProposal(title=title,reason='Fixture.',patches=patches)
                return WindTunnelSuggestion(run_id=run_id,proposal_key=uuid4().hex,status='ready',payload=dict(proposal=proposal.model_dump(mode='json'),validation=dict(requires_recruiter_review=True)))
            linked = row(first.id,'Deployment wording',[
                dict(kind='criterion',criterion_id='docker',field='text',expected='Docker and CI/CD experience.',proposed='Experience using Docker and CI/CD workflows.'),
                dict(kind='description',expected='Docker and CI/CD experience.',proposed='Experience using Docker and CI/CD workflows.')])
            api = row(first.id,'API wording',[dict(kind='description',expected='Build REST APIs.',proposed='Develop REST APIs.')])
            third = row(first.id,'Maintenance wording',[dict(kind='description',expected='Maintain services.',proposed='Maintain backend services.')])
            foreign_batch = row(other.id,'Other batch',[dict(kind='description',expected='Build REST APIs.',proposed='Implement REST APIs.')])
            db.add_all([linked,api,third,foreign_batch]); await db.commit()
            job_id, owner_id = job.id, user.id
            selected_ids = [linked.id,api.id]; all_ids = [*selected_ids,third.id]; other_id=foreign_batch.id
            preview,_ = await review.preview(db,job_id,owner_id,selected_ids,version)
            self.assertIn('Develop REST APIs.',preview['edit']['content'])
            self.assertIn('CI/CD workflows',preview['edit']['content'])
            await db.commit()  # Leaving/cancelling preview still saves no edits.
            self.assertEqual((await db.get(JobDescription,job_id)).content,content)
            for ids,who,expected_code in [(selected_ids,uuid4(),404),([selected_ids[0],selected_ids[0]],owner_id,422),([selected_ids[0],other_id],owner_id,422)]:
                with self.assertRaises(HTTPException) as caught:
                    await review.preview(db,job_id,who,ids,version)
                self.assertEqual(caught.exception.status_code,expected_code)
            with self.assertRaises(HTTPException) as caught:
                await review.apply(db,None,job_id,owner_id,selected_ids,version,preview['review_token'],False)
            self.assertEqual(caught.exception.status_code,422)
            with self.assertRaises(HTTPException) as caught:
                await review.apply(db,None,job_id,owner_id,selected_ids,version,'0'*64,True)
            self.assertEqual(caught.exception.status_code,409)
            with self.assertRaises(HTTPException) as caught:
                await review.apply(db,None,job_id,owner_id,[selected_ids[0]],version,preview['review_token'],True)
            self.assertEqual(caught.exception.status_code,409)
            graph=NS(get_state=lambda config:NS(values={}))
            with patch('app.services.jd.editing.manual_context',side_effect=ValueError('FIXTURE_AFTER_MARKERS')):
                with self.assertRaisesRegex(ValueError,'FIXTURE_AFTER_MARKERS'):
                    await review.apply(db,graph,job_id,owner_id,selected_ids,version,preview['review_token'],True)
            await db.rollback()
            async with self.factory() as fresh:
                unchanged=await fresh.get(JobDescription,job_id)
                self.assertEqual(unchanged.content,content)
                self.assertEqual(unchanged.profile_version,1)
                statuses=list(await fresh.scalars(select(WindTunnelSuggestion.status)))
                self.assertEqual(statuses.count('ready'),4)
            with patch('app.services.jd.editing.load_jd_conversation',new=AsyncMock(return_value={'status':'approved'})):
                await review.apply(db,graph,job_id,owner_id,selected_ids,version,preview['review_token'],True)
            changed=await db.get(JobDescription,job_id,populate_existing=True)
            self.assertEqual(changed.profile_version,2)
            self.assertIn('Develop REST APIs.',changed.content)
            self.assertEqual(changed.screening_profile['criteria'][0]['text'],'Experience using Docker and CI/CD workflows.')
            rows=list(await db.scalars(select(WindTunnelSuggestion)))
            self.assertEqual({r.id for r in rows if r.status=='applied'},set(selected_ids))
            self.assertTrue(all(r.status=='stale' for r in rows if r.id not in selected_ids))
            with self.assertRaises(HTTPException) as caught:
                await review.apply(db,graph,job_id,owner_id,selected_ids,version,preview['review_token'],True)
            self.assertEqual(caught.exception.status_code,409)
            db.add(DiscordJobPost(job_description_id=job_id,status='sending')); await db.commit()
            with self.assertRaises(HTTPException) as caught:
                await review.preview(db,job_id,owner_id,[all_ids[-1]],document_edit_version(changed))
            self.assertEqual(caught.exception.status_code,409)


@unittest.skipUnless(os.environ.get('RUN_F11_MODEL_TESTS') == '1', 'Opt in to live model calls with synthetic data only')
class LiveModelAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_exact_edits_from_realistic_synthetic_screening_results(self):
        import json
        from app.integrations.cv_model import ModelExecution, provider, model_name, context_tokens
        from app.services.wind_tunnel.storage import JobSnapshot
        from app.services.jd.edit_policy import document_edit_version
        content = '# Backend Developer\n\n## Responsibilities\n- Develop REST APIs using Python and FastAPI.\n- Write automated tests and maintain existing services.\n- Package applications with Docker and contribute to CI/CD pipelines.\n\n## Employment\nFull-time hybrid role. Salary and benefits are provided separately.'
        texts=['Python and FastAPI experience.','Docker and CI/CD experience.','Automated testing experience.']
        criteria=[{'id':f'fixture-{i}','text':v,'dimension':'technical_skills','priority':'Must have' if i==0 else 'Preferred','source':'recruiter','source_quote':v,'priority_source':'recruiter','priority_quote':('Must have: ' if i==0 else 'Preferred: ')+v} for i,v in enumerate(texts)]
        profile={'criteria':criteria,'addressed_dimensions':['technical_skills','work_experience','qualifications','additional_criteria'],'dimension_weights':{'technical_skills':100,'work_experience':0,'qualifications':0,'additional_criteria':0},'weights_confirmed':True,'weights_confirmation_quote':'Confirmed technical skills 100%','minimum_years_experience':None,'fresher_eligibility':None,'google_form_url':None}
        snapshot={'content':content,'screening_profile':profile,'profile_version':1}
        findings,evidence,observations={},{},[]
        for persona in ['strong_all_rounder','keyword_stuffer','near_miss']:
            aid=str(uuid4())
            for i,c in enumerate(criteria):
                fid=f'fixture-{persona}-{i}'; decision='insufficient_evidence' if persona=='near_miss' and i==0 else 'met'
                ref={'assessment_id':aid,'requirement_id':c['id']}
                findings[fid]={'id':fid,'summary':f'{persona}: {c["text"]} decision {decision}; synthetic observation, not proof of a defect.','references':[ref]}
                evidence[(aid,c['id'])]={**ref,'match':{'decision':decision,'justification':'A stated skill listing satisfies this broadly worded criterion.' if persona=='keyword_stuffer' else 'The synthetic CV contains role-relevant use evidence.' if decision=='met' else 'The synthetic CV omits the required skill.','unsupported_requirement_parts':[],'evidence':[]}}
                observations.append({'finding_id':fid,'persona':persona,'criterion_id':c['id'],'decisions':[decision],'status':'observed' if persona=='keyword_stuffer' else 'expected'})
        report={'coverage':{'expected_assessments':3,'completed_assessments':3,'complete_cohorts':1},'criterion_observations':observations,'comparisons':[],'personas':{},'weight_sensitivity':{'tested_vectors':[],'finding_ids':list(findings)}}
        context=Context(snapshot,document_edit_version(JobSnapshot(**snapshot)),'synthetic-fixture',report,findings,evidence,{})
        execution=ModelExecution(provider=provider(),model=model_name(),context_tokens=context_tokens())
        state={}
        for attempt in range(1,suggestions.MAX_ATTEMPTS+1):
            state['attempt']=attempt
            try:
                batch=await generate_proposal_batch(context,execution,state)
                state['draft']=batch.model_dump(mode='json')
                proposals,preview=reconcile(batch,context)
                self.assertTrue(proposals)
                raw=await suggestions.model_call(suggestions.REVIEW_PROMPT,{**context.payload(),'combined_preview':preview,'candidates':[{'candidate_id':candidate_id(p),**p.model_dump(mode='json')} for p in proposals]},suggestions.BatchReview.model_json_schema(),execution)
                validate_review(raw,proposals)
                print(json.dumps({'synthetic_fixture_only':True,'exact_edits':[p.proposal.model_dump(mode='json') for p in proposals]}),flush=True)
                return
            except (ValueError, suggestions.HTTPException) as exc:
                state=failure(state,exc,'review' if state.get('draft') else 'generate')
                print(json.dumps({'attempt':attempt,'error':str(exc),'validation_details':state.get('validation_details',[]),'synthetic_draft':state.get('draft')}),flush=True)
                if state['status']!='repair':
                    raise
        self.fail('No validated exact edit after bounded repair')


if __name__ == '__main__':
    unittest.main()
