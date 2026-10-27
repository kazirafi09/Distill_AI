"""Reference selections copy exact source whitespace and preserve cached contracts."""
from dataclasses import asdict
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.integrations.cv_model import ModelExecution
from app.services.interviews import analysis
from app.services.interviews.assessment_runs import recovery_policy


class ReferenceSelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_reference_recovery_is_limited_to_evaluation_stages(self):
        failure = {'last_error': 'REFERENCE_QUOTE_INVALID'}
        self.assertEqual(recovery_policy('evaluation-g1-accuracy-attempts', failure), 'reference-selection-recovery-1')
        self.assertIsNone(recovery_policy('grouping-attempts', failure))

    def setUp(self):
        self.reference = {'id': 'bank:source', 'text': 'Use thin controllers.\n       Validate with Form Requests.',
                          'url': 'urn:test:bank', 'version': 'v1'}
        self.state = {'groups': [{'id': 'g1', 'topic': 'Laravel', 'answer_state': 'answered',
            'question_turn_ids': ['q'], 'answer_turn_ids': ['a'], 'criterion_ids': ['laravel']}],
            'group_index': 0, 'turns': [
                {'id': 'q', 'speaker_role': 'recruiter', 'text': 'How do you validate?', 'session_start_ms': None, 'session_end_ms': None},
                {'id': 'a', 'speaker_role': 'candidate', 'text': 'I use Form Requests.', 'session_start_ms': None, 'session_end_ms': None}],
            'criteria': [{'id': 'laravel', 'text': 'Laravel'}], 'answer_references': [self.reference],
            'prepared': {'source_transcript_id': 'transcript'},
            'model_options': asdict(ModelExecution('gemini', 'test'))}
        self.output = {'dimension': 'accuracy', 'score': 3, 'reason': 'ASSESSED',
            'justification': 'The answer matches the reference.', 'evidence': ['e1'],
            'accuracy_basis': 'reference_supported', 'reference_evidence': ['r1']}

    async def test_selected_reference_keeps_source_newlines_and_offsets(self):
        with patch.object(analysis, 'stage_read', AsyncMock(return_value=None)), \
             patch.object(analysis, 'stage_write', AsyncMock()), \
             patch.object(analysis, 'generate_json', AsyncMock(return_value=json.dumps(self.output))) as generate:
            result = await analysis.evaluate_dimension(self.state, 'accuracy')
        citation = result['result']['reference_evidence'][0]
        self.assertEqual(citation['quote'], self.reference['text'])
        self.assertEqual(citation['start_char'], 0)
        self.assertEqual(citation['end_char'], len(self.reference['text']))
        self.assertEqual(citation['url'], self.reference['url'])
        schema = generate.call_args.args[2]
        self.assertEqual(schema['properties']['reference_evidence']['items']['enum'], ['r1'])

    async def test_unknown_reference_selection_is_rejected_after_bounded_repair(self):
        with patch.object(analysis, 'stage_read', AsyncMock(return_value=None)), \
             patch.object(analysis, 'stage_write', AsyncMock()) as write, \
             patch.object(analysis, 'generate_json', AsyncMock(return_value=json.dumps({**self.output, 'reference_evidence': ['invented']}))) as generate:
            with self.assertRaisesRegex(analysis.AnalysisError, 'ANALYSIS_OUTPUT_NEEDS_REVIEW'):
                await analysis.evaluate_dimension(self.state, 'accuracy')
        self.assertEqual(generate.await_count, 2)
        self.assertEqual(write.call_args.args[2]['last_error'], 'REFERENCE_SELECTION_INVALID')

    async def test_previously_accepted_mixed_contract_is_read_without_a_model_call(self):
        cached = {**self.output, 'reference_evidence': [{'reference_id': self.reference['id'], 'quote': self.reference['text']}]}
        with patch.object(analysis, 'stage_read', AsyncMock(return_value=cached)), \
             patch.object(analysis, 'generate_json', AsyncMock()) as generate:
            result = await analysis.evaluate_dimension(self.state, 'accuracy')
        self.assertEqual(result['result']['reference_evidence'][0]['quote'], self.reference['text'])
        generate.assert_not_awaited()

    async def test_no_references_cannot_generate_a_citation(self):
        self.state['answer_references'] = []
        output = {**self.output, 'accuracy_basis': 'model_knowledge', 'reference_evidence': []}
        with patch.object(analysis, 'stage_read', AsyncMock(return_value=None)), \
             patch.object(analysis, 'stage_write', AsyncMock()), \
             patch.object(analysis, 'generate_json', AsyncMock(return_value=json.dumps(output))) as generate:
            result = await analysis.evaluate_dimension(self.state, 'accuracy')
        self.assertEqual(result['result']['reference_evidence'], [])
        self.assertEqual(generate.call_args.args[2]['properties']['reference_evidence']['maxItems'], 0)


if __name__ == '__main__':
    unittest.main()
