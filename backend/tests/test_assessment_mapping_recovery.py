"""Exact mapping contracts and bounded PostgreSQL recovery without optional fixtures."""
import json
import os
import unittest
from dataclasses import asdict
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


class MappingContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_ids_are_constrained_and_invalid_prefix_is_repaired(self):
        state = {'criteria': [{'id': 'criterion-python', 'text': 'Python'}],
            'groups': [{'id': 'g1', 'topic': 'Python', 'question_turn_ids': ['q'], 'answer_turn_ids': ['a']}],
            'turns': [{'id': 'q', 'speaker_role': 'recruiter', 'text': 'Which language?'},
                      {'id': 'a', 'speaker_role': 'candidate', 'text': 'Python.'}],
            'model_options': asdict(ModelExecution('gemini', 'fixture'))}
        outputs = [json.dumps({'mappings': [{'group_id': 'g1', 'criterion_ids': [identifier]}]})
                   for identifier in ('python', 'criterion-python')]
        with patch.object(analysis, 'stage_read', AsyncMock(return_value=None)), \
             patch.object(analysis, 'stage_write', AsyncMock()), \
             patch.object(analysis, 'generate_json', AsyncMock(side_effect=outputs)) as generate:
            result = await analysis.classify_groups(state)
        self.assertEqual(result[0]['criterion_ids'], ['criterion-python'])
        self.assertEqual(generate.await_count, 2)
        props = generate.call_args_list[0].args[2]['$defs']['RequirementMapping']['properties']
        self.assertEqual(props['criterion_ids']['items']['enum'], ['criterion-python'])
        self.assertEqual(props['group_id']['enum'], ['g1'])
        self.assertIn('allowed_criterion_ids', generate.call_args_list[1].args[0])
        self.assertIn('criterion-python', generate.call_args_list[1].args[0])


@unittest.skipUnless(os.environ.get('RUN_ASSESSMENT_DB_TESTS') == '1', 'Opt in to PostgreSQL checks')
class MappingRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner = uuid4()
        self.addAsyncCleanup(self.cleanup)
        async with SessionFactory() as db:
            db.add(User(id=self.owner, email=f'mapping-{self.owner}@example.invalid', password_hash='fixture', role='recruiter'))
            await db.flush()
            conversation = JDConversation(recruiter_id=self.owner)
            db.add(conversation)
            await db.flush()
            job = JobDescription(conversation_id=conversation.id, title='Synthetic mapping test', content='Fixture', requirements='Fixture')
            db.add(job)
            await db.flush()
            source = AssessmentRun(recruiter_id=self.owner, job_id=job.id, kind='f5', synthetic=True,
                label='Synthetic mapping fixture', status='needs_review', stage='needs_review',
                error_code='ANALYSIS_OUTPUT_NEEDS_REVIEW', input_key=uuid4().hex, total_groups=1,
                inputs={'prompt_version': analysis.PROMPT_VERSION, 'rubric_version': RUBRIC_VERSION})
            db.add(source)
            await db.flush()
            self.source = source.id
            self.grouping = {'groups': [{'id': 'g1'}]}
            db.add_all([AssessmentStage(run_id=source.id, name='grouping', payload=self.grouping),
                AssessmentStage(run_id=source.id, name='mapping-attempts',
                    payload={'count': 2, 'last_error': 'MAPPING_CRITERION_INVALID'})])
            await db.commit()

    async def cleanup(self):
        async with SessionFactory() as db:
            await db.execute(delete(User).where(User.id == self.owner))
            await db.commit()
        await engine.dispose()

    async def test_mapping_recovery_reuses_groups_once_and_preserves_history(self):
        first = await runs.recover_run(self.source, self.owner)
        second = await runs.recover_run(self.source, self.owner)
        self.assertEqual(first['id'], second['id'])
        child_id = UUID(first['id'])
        async with SessionFactory() as db:
            child = await db.get(AssessmentRun, child_id)
            self.assertEqual(child.inputs['recovery']['policy'], 'mapping-id-recovery-1')
            self.assertEqual(child.inputs['recovery']['retry_stage'], 'mapping')
            self.assertEqual((await db.get(AssessmentStage, (child_id, 'grouping'))).payload, self.grouping)
            self.assertIsNone(await db.get(AssessmentStage, (child_id, 'mapping-attempts')))
            self.assertEqual((await db.get(AssessmentStage, (self.source, 'mapping-attempts'))).payload['count'], 2)
            child.status, child.error_code = 'needs_review', 'ANALYSIS_OUTPUT_NEEDS_REVIEW'
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.recover_run(child_id, self.owner)

    async def test_accepted_mapping_cannot_be_reset(self):
        async with SessionFactory() as db:
            db.add(AssessmentStage(run_id=self.source, name='mapping', payload={'mappings': []}))
            await db.commit()
        with self.assertRaises(HTTPException):
            await runs.recover_run(self.source, self.owner)


if __name__ == '__main__':
    unittest.main()
