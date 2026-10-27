"""Speaker-role repair must preserve source roles and keep recovery bounded."""
from copy import deepcopy
from dataclasses import asdict
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import delete

from app.db.session import SessionFactory, engine
from app.integrations.cv_model import ModelExecution
from app.models.user import User
from app.models.jd import JDConversation, JobDescription
from app.models.assessments import AssessmentRun, AssessmentStage
from app.services.interviews import analysis, assessment_runs as runs
from app.services.interviews.rubric import RUBRIC_VERSION


class GroupingRoleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.state = {'turns': [
            {'id': 'ack', 'speaker_role': 'candidate', 'text': 'Okay.'},
            {'id': 'q', 'speaker_role': 'recruiter', 'text': 'How do you use Git?'},
            {'id': 'a', 'speaker_role': 'candidate', 'text': 'I open a pull request.'}],
            'model_options': asdict(ModelExecution('gemini', 'fixture'))}
        for turn in self.state['turns']:
            turn.update(session_start_ms=None, session_end_ms=None)
        self.valid = {'groups': [{'id': 'g1', 'topic': 'Git', 'answer_state': 'answered'}],
                      'assignments': ['exclude:social', 'g1:question', 'g1:answer']}
        self.invalid = {**self.valid, 'assignments': ['g1:question', 'g1:question', 'g1:answer']}
        self.stages = {}

    async def read(self, state, name):
        return deepcopy(self.stages.get(name))

    async def write(self, state, name, payload):
        self.stages[name] = deepcopy(payload)

    async def invoke(self, outputs):
        with patch.object(analysis, 'stage_read', side_effect=self.read), \
             patch.object(analysis, 'stage_write', side_effect=self.write), \
             patch.object(analysis, 'generate_json', AsyncMock(side_effect=[json.dumps(x) for x in outputs])) as generate:
            result = await analysis.segment_questions(self.state)
        return result, generate

    async def test_candidate_acknowledgement_repair_identifies_exact_conflict(self):
        result, generate = await self.invoke([self.invalid, self.valid])
        self.assertEqual(result['groups'][0]['question_turn_ids'], ['q'])
        self.assertEqual(result['excluded_turns'], [{'turn_id': 'ack', 'reason': 'social'}])
        diagnostics = self.stages['grouping-attempts']['last_details']
        self.assertEqual(diagnostics['turn_id'], 't001')
        self.assertEqual(diagnostics['speaker_role'], 'candidate')
        self.assertEqual(diagnostics['expected_speaker_role'], 'recruiter')
        self.assertIn('t001', generate.call_args_list[1].args[0])
        self.assertEqual(self.state['turns'][0]['speaker_role'], 'candidate')

    async def test_recruiter_cannot_be_assigned_as_candidate_answer(self):
        invalid = {**self.valid, 'assignments': ['g1:question', 'g1:answer', 'g1:answer']}
        # Keep a valid recruiter root so the conflicting answer is also checked.
        self.state['turns'][0]['speaker_role'] = 'recruiter'
        await self.invoke([invalid, self.valid])
        self.assertEqual(self.stages['grouping-attempts']['last_details']['turn_id'], 't002')
        self.assertEqual(self.stages['grouping-attempts']['last_details']['expected_speaker_role'], 'candidate')

    async def test_recovery_uses_original_diagnostics_on_first_call_and_stops_after_two(self):
        self.state['recovery'] = {'policy': 'grouping-role-recovery-1', 'retry_stage': 'grouping'}
        source = {'count': 2, 'last_error': 'GROUP_ROLE_INVALID', 'last_details': {},
                  'previous_invalid_output': self.invalid}
        self.stages['recovery-source-diagnostics'] = deepcopy(source)
        with self.assertRaisesRegex(analysis.AnalysisError, 'ANALYSIS_OUTPUT_NEEDS_REVIEW'):
            await self.invoke([self.invalid, self.invalid])
        self.assertEqual(self.stages['grouping-attempts']['count'], 2)
        self.assertEqual(self.stages['recovery-source-diagnostics'], source)
        with patch.object(analysis, 'stage_read', side_effect=self.read), \
             patch.object(analysis, 'generate_json', AsyncMock()) as generate:
            with self.assertRaisesRegex(analysis.AnalysisError, 'ANALYSIS_OUTPUT_NEEDS_REVIEW'):
                await analysis.segment_questions(self.state)
            generate.assert_not_awaited()

    async def test_recovery_first_request_contains_previous_invalid_output(self):
        self.state['recovery'] = {'policy': 'grouping-role-recovery-1', 'retry_stage': 'grouping'}
        self.stages['recovery-source-diagnostics'] = {'count': 2, 'last_error': 'GROUP_ROLE_INVALID',
            'last_details': {'turn_id': 't001'}, 'previous_invalid_output': self.invalid}
        _, generate = await self.invoke([self.valid])
        self.assertIn('GROUP_ROLE_INVALID', generate.call_args.args[0])
        self.assertEqual(json.loads(generate.call_args.args[1])['previous_invalid_output'], self.invalid)
        self.assertEqual(self.stages['grouping-attempts']['count'], 1)

    async def test_missing_question_is_not_invented_to_accept_a_rootless_group(self):
        rootless = {'groups': [
            {'id': 'g1', 'topic': 'Unrecorded opening question', 'answer_state': 'answered'},
            {'id': 'g2', 'topic': 'Git', 'answer_state': 'answered'}],
            'assignments': ['g1:answer', 'g2:question', 'g2:answer']}
        with self.assertRaisesRegex(analysis.AnalysisError, 'ANALYSIS_OUTPUT_NEEDS_REVIEW'):
            await self.invoke([rootless, rootless])
        self.assertEqual(self.stages['grouping-attempts']['last_error'], 'GROUP_ROOT_MISSING')
        self.assertNotIn('grouping', self.stages)


@unittest.skipUnless(os.environ.get('RUN_ASSESSMENT_DB_TESTS') == '1', 'Opt in to PostgreSQL checks')
class GroupingRecoveryDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = uuid4()
        self.addAsyncCleanup(self.cleanup)
        async with SessionFactory() as db:
            db.add(User(id=self.owner, email=f'grouping-{self.owner}@example.invalid', password_hash='fixture', role='recruiter'))
            await db.flush()
            conversation = JDConversation(recruiter_id=self.owner)
            db.add(conversation)
            await db.flush()
            job = JobDescription(conversation_id=conversation.id, title='Grouping test', content='Fixture', requirements='Fixture')
            db.add(job)
            await db.flush()
            source = AssessmentRun(recruiter_id=self.owner, job_id=job.id, kind='f5', synthetic=True,
                label='Synthetic grouping test', status='needs_review', stage='needs_review',
                error_code='ANALYSIS_OUTPUT_NEEDS_REVIEW', input_key=uuid4().hex,
                inputs={'prompt_version': 'interview-analysis-14', 'rubric_version': RUBRIC_VERSION})
            db.add(source)
            await db.flush()
            self.source = source.id
            self.diagnostics = {'count': 2, 'last_error': 'GROUP_ROLE_INVALID', 'last_details': {},
                                'previous_invalid_output': {'assignments': ['g1:question']}}
            db.add(AssessmentStage(run_id=source.id, name='grouping-attempts', payload=self.diagnostics))
            await db.commit()

    async def cleanup(self):
        async with SessionFactory() as db:
            await db.execute(delete(User).where(User.id == self.owner))
            await db.commit()
        await engine.dispose()

    async def test_legacy_recovery_is_idempotent_and_preserves_original_failure(self):
        first = await runs.recover_run(self.source, self.owner)
        self.assertEqual((await runs.recover_run(self.source, self.owner))['id'], first['id'])
        child_id = UUID(first['id'])
        async with SessionFactory() as db:
            child = await db.get(AssessmentRun, child_id)
            self.assertEqual(child.inputs['prompt_version'], analysis.PROMPT_VERSION)
            self.assertEqual(child.inputs['recovery']['policy'], 'grouping-role-recovery-1')
            self.assertIsNone(await db.get(AssessmentStage, (child_id, 'grouping-attempts')))
            self.assertEqual((await db.get(AssessmentStage, (child_id, 'recovery-source-diagnostics'))).payload, self.diagnostics)
            self.assertEqual((await db.get(AssessmentStage, (self.source, 'grouping-attempts'))).payload, self.diagnostics)
            child.status, child.error_code = 'needs_review', 'ANALYSIS_OUTPUT_NEEDS_REVIEW'
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.recover_run(child_id, self.owner)

    async def test_accepted_grouping_cannot_be_reset(self):
        async with SessionFactory() as db:
            db.add(AssessmentStage(run_id=self.source, name='grouping', payload={'groups': []}))
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.recover_run(self.source, self.owner)

    def test_unknown_grouping_failures_are_not_recoverable(self):
        self.assertIsNone(runs.recovery_policy('grouping-attempts', {'last_error': 'UNKNOWN'}))

    async def test_explicit_rerun_creates_one_fresh_owned_run_without_resetting_source(self):
        async with SessionFactory() as db:
            source = await db.get(AssessmentRun, self.source)
            source.synthetic = False
            source.inputs = {**source.inputs, 'prepared': {'source_transcript_id': 'pinned'},
                             'recovery': {'source_run_id': 'earlier'}}
            await db.commit()
        with self.assertRaises(HTTPException) as denied:
            await runs.rerun_run(self.source, uuid4())
        self.assertEqual(denied.exception.status_code, 404)
        first = await runs.rerun_run(self.source, self.owner)
        self.assertEqual((await runs.rerun_run(self.source, self.owner))['id'], first['id'])
        self.assertNotEqual(first['id'], str(self.source))
        async with SessionFactory() as db:
            child = await db.get(AssessmentRun, UUID(first['id']))
            self.assertEqual(child.inputs['prepared'], {'source_transcript_id': 'pinned'})
            self.assertNotIn('recovery', child.inputs)
            self.assertEqual(child.parent_run_id, self.source)
            self.assertEqual(child.attempt, 0)
            self.assertEqual((await db.get(AssessmentRun, self.source)).status, 'needs_review')
            self.assertEqual((await db.get(AssessmentStage, (self.source, 'grouping-attempts'))).payload, self.diagnostics)

    async def test_rerun_rejects_active_runs_and_synthetic_samples(self):
        with self.assertRaises(HTTPException):
            await runs.rerun_run(self.source, self.owner)
        async with SessionFactory() as db:
            source = await db.get(AssessmentRun, self.source)
            source.synthetic, source.status = False, 'running'
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.rerun_run(self.source, self.owner)
