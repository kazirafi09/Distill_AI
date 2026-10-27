"""Quality findings bind selected excerpts to their actual saved question groups."""
from copy import deepcopy
import unittest

from app.services.interviews import post_interview_analysis as service


class QualitySelectionTests(unittest.TestCase):
    def setUp(self):
        self.inputs = {'rubric_version': 'interview-quality-2', 'criteria': [],
            'prepared': {'text_only': True, 'turns': [
                {'id': 'q1', 'speaker_role': 'recruiter', 'text': 'Describe Laravel.\nPlease.'},
                {'id': 'a1', 'speaker_role': 'candidate', 'text': 'It is a PHP framework.'},
                {'id': 'q2', 'speaker_role': 'recruiter', 'text': 'How do you use Git?'},
                {'id': 'outside', 'speaker_role': 'candidate', 'text': 'Goodbye.'}]},
            'f5': {'results': [
                {'group': {'id': 'g1', 'question_turn_ids': ['q1'], 'answer_turn_ids': ['a1'], 'criterion_ids': []}},
                {'group': {'id': 'g2', 'question_turn_ids': ['q2'], 'answer_turn_ids': [], 'criterion_ids': []}}]}}
        self.raw = {'summary': 'Review the depth of follow-up.', 'findings': [{
            'category': 'follow_up', 'observation': 'The short answer has no follow-up.',
            'evidence': ['quality-source-1', 'quality-source-2'],
            'improvement': 'Ask for an implementation example.', 'uncertainty': None}]}

    def test_exact_question_and_answer_are_bound_to_their_saved_group(self):
        before = deepcopy((self.inputs, self.raw))
        result = service.materialize('interview_quality', self.raw, self.inputs)
        finding = result['findings'][0]
        self.assertEqual(finding['group_ids'], ['g1'])
        self.assertEqual([e['speaker_role'] for e in finding['evidence']], ['recruiter', 'candidate'])
        self.assertEqual(finding['evidence'][0]['quote'], 'Describe Laravel.\nPlease.')
        self.assertEqual((self.inputs, self.raw), before)

    def test_multiple_groups_are_derived_from_selected_sources(self):
        self.raw['findings'][0]['evidence'].append('quality-source-3')
        result = service.materialize('interview_quality', self.raw, self.inputs)
        self.assertEqual(result['findings'][0]['group_ids'], ['g1', 'g2'])

    def test_schema_only_allows_known_group_bound_references(self):
        schema = service.generation_schema('interview_quality', self.inputs)
        props = schema['$defs']['SelectedQualityFinding']['properties']
        self.assertNotIn('group_ids', props)
        self.assertEqual(props['evidence']['items']['enum'], ['quality-source-1', 'quality-source-2', 'quality-source-3'])
        self.assertNotIn('outside', str(service.quality_excerpts(self.inputs)))

    def test_unknown_or_duplicate_selections_are_rejected(self):
        for evidence in (['invented'], ['quality-source-1', 'quality-source-1']):
            with self.subTest(evidence=evidence):
                self.raw['findings'][0]['evidence'] = evidence
                with self.assertRaisesRegex(ValueError, 'INTERVIEW_QUALITY_SOURCE_INVALID'):
                    service.materialize('interview_quality', self.raw, self.inputs)

    def test_legacy_group_scope_validation_still_rejects_cross_group_evidence(self):
        self.inputs['rubric_version'] = 'interview-quality-1'
        self.raw['findings'][0].update(group_ids=['g1'], evidence=[{'turn_id': 'q2', 'quote': 'How do you use Git?'}])
        with self.assertRaisesRegex(ValueError, 'INTERVIEW_QUALITY_GROUP_EVIDENCE_INVALID'):
            service.materialize('interview_quality', self.raw, self.inputs)


if __name__ == '__main__':
    unittest.main()
