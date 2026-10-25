"""F11 document and reviewer repair boundaries; model calls are fixtures by default."""
import json
import os
import unittest
from copy import deepcopy
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from app.integrations.cv_model import ModelExecution
from app.services.wind_tunnel import generation as g
from app.services.documents.extraction import extract_document


CRITERIA = [{'id': 'python', 'text': 'Python experience.', 'dimension': 'technical_skills', 'priority': 'Must have'}]
PLAN = {'personas': {'strong_all_rounder': {'criterion_ids': ['python']}, 'keyword_stuffer': {'criterion_ids': ['python']}}}
TEXT = 'Candidate 001\nSkills\nPython\nExperience\nBuilt Python services for a fictional internal project.'


def review(support='demonstrated', **changes):
    return {'criteria': [{'criterion_id': 'python', 'support': support, 'evidence_ids': ['p5'] if support == 'demonstrated' else ['p3'] if support == 'listed' else [], 'quotes': []}],
            'faithful': True, 'metadata_leak': False, 'preserves_other_facts': True, **changes}


def draft():
    return {'sections': [{'heading': 'Summary', 'paragraphs': ['Backend developer building internal services and collaborating on maintainable software for fictional projects.']},
                         {'heading': 'Experience', 'paragraphs': ['Built Python services for a fictional internal project and maintained its API implementation.']}]}


class GenerationContractTests(unittest.TestCase):
    def test_blank_sections_and_crlf_are_normalized_without_losing_content(self):
        raw = draft()
        raw['sections'][0]['paragraphs'] += [' ', '\r\n']
        raw['sections'][1]['paragraphs'][0] += '\r\nReviewed software changes.'
        raw['sections'].append({'heading': 'Certifications', 'paragraphs': ['']})
        checked = g.prepare_draft(raw)
        self.assertEqual(len(checked['sections']), 2)
        self.assertNotIn('\r', checked['sections'][1]['paragraphs'][0])
        self.assertIn('Reviewed software changes.', checked['sections'][1]['paragraphs'][0])
        data = g.render_docx(checked, 1)
        extracted = extract_document('fixture.docx', data)
        self.assertIn('Built Python services', extracted.text)

    def test_format_diagnostics_are_specific_and_do_not_contain_prose(self):
        for value, issue in [('x' * 1501, 'paragraph_too_long'), ('private\x00prose' * 12, 'control_character')]:
            raw = draft(); raw['sections'][0]['paragraphs'] = [value]
            with self.assertRaises(g.CVValidationError) as caught:
                g.prepare_draft(raw)
            self.assertIn(issue, str(caught.exception.details))
            self.assertNotIn(value, str(caught.exception.details))
        raw = {'sections': [{'heading': 'Summary', 'paragraphs': ['a']}, {'heading': 'Skills', 'paragraphs': ['b']}]}
        with self.assertRaises(g.CVValidationError) as caught:
            g.prepare_draft(raw)
        self.assertEqual(caught.exception.details[0]['issue'], 'total_prose_length')

    def test_schema_diagnostics_do_not_log_rejected_fields(self):
        with self.assertRaises(g.CVValidationError) as caught:
            g.prepare_draft({'sections': [{'private text': 'secret'}]})
        self.assertNotIn('secret', str(caught.exception.details))
        self.assertNotIn('private text', str(caught.exception.details))

    def test_passage_ids_resolve_to_exact_quotes(self):
        result = g.validate_review(review(), TEXT, CRITERIA, 'strong_all_rounder', PLAN)
        self.assertEqual(result['criteria'][0]['quotes'], [TEXT.splitlines()[4]])

    def test_absent_evidence_and_missing_supported_evidence_have_distinct_feedback(self):
        raw = review('absent'); raw['criteria'][0]['evidence_ids'] = ['p3']
        with self.assertRaises(g.ReviewContractError) as caught:
            g.validate_review(raw, TEXT, CRITERIA, 'keyword_stuffer', PLAN)
        self.assertEqual(caught.exception.details[0]['issue'], 'absent_requires_empty_evidence')
        raw = review(); raw['criteria'][0]['evidence_ids'] = []
        with self.assertRaises(g.ReviewContractError) as caught:
            g.validate_review(raw, TEXT, CRITERIA, 'strong_all_rounder', PLAN)
        self.assertEqual(caught.exception.details[0]['issue'], 'supported_requires_evidence')

    def test_unknown_ids_and_inexact_quotes_are_rejected_not_fuzzy_matched(self):
        for ids, quotes, issue in [(['p999'], [], 'unknown_passage_id'), ([], ['Built imaginary Python software'], 'quote_not_exact_use_passage_ids')]:
            raw = review(); raw['criteria'][0].update(evidence_ids=ids, quotes=quotes)
            with self.assertRaises(g.ReviewContractError) as caught:
                g.validate_review(raw, TEXT, CRITERIA, 'strong_all_rounder', PLAN)
            self.assertEqual(caught.exception.details[0]['issue'], issue)
            self.assertNotIn('imaginary', str(caught.exception.details))

    def test_coverage_feedback_and_content_mismatch_are_separate(self):
        with self.assertRaises(g.ReviewContractError) as caught:
            g.validate_review(review(criteria=[]), TEXT, CRITERIA, 'strong_all_rounder', PLAN)
        self.assertEqual(caught.exception.details[0]['missing_ids'], ['python'])
        with self.assertRaises(g.PersonaDifferenceError) as caught:
            g.validate_review(review(), TEXT, CRITERIA, 'keyword_stuffer', PLAN)
        self.assertEqual(caught.exception.details[0]['expected_support'], 'listed')
        self.assertEqual(caught.exception.evidence[0]['quotes'], [TEXT.splitlines()[4]])


class ReviewRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_review_is_repaired_against_the_same_document(self):
        bad = review(); bad['criteria'][0]['quotes'] = ['not in the document']
        saved = AsyncMock()
        with patch.object(g, 'model_call', new=AsyncMock(side_effect=[bad, review()])) as model:
            result = await g.review_document({'persona': 'strong_all_rounder', 'previous_draft': {'untrusted': 'old CV'}, 'repair_evidence': ['old quote']}, TEXT, CRITERIA, 'strong_all_rounder', PLAN, None, on_failure=saved)
        self.assertEqual(model.await_count, 2)
        first, second = (call.args[1] for call in model.call_args_list)
        self.assertEqual(first['cv_passages'], second['cv_passages'])
        self.assertEqual(second['review_repair_details'][0]['issue'], 'quote_not_exact_use_passage_ids')
        self.assertEqual(second['previous_review'], bad)
        self.assertNotIn('previous_draft', second)
        self.assertNotIn('repair_evidence', second)
        saved.assert_awaited_once()
        self.assertEqual(result['criteria'][0]['support'], 'demonstrated')

    async def test_json_and_schema_errors_can_be_repaired(self):
        for failure in (json.JSONDecodeError('bad', '', 0), {'criteria': None}):
            with patch.object(g, 'model_call', new=AsyncMock(side_effect=[failure, review()])) as model:
                await g.review_document({}, TEXT, CRITERIA, 'strong_all_rounder', PLAN, None)
            self.assertEqual(model.await_count, 2)

    async def test_review_repair_is_bounded_and_does_not_change_verdict_to_match_persona(self):
        bad = review(); bad['criteria'][0]['evidence_ids'] = []
        with patch.object(g, 'model_call', new=AsyncMock(return_value=bad)) as model:
            with self.assertRaises(g.ReviewContractError):
                await g.review_document({}, TEXT, CRITERIA, 'strong_all_rounder', PLAN, None)
            self.assertEqual(model.await_count, g.MAX_REVIEW_ATTEMPTS)
        with patch.object(g, 'model_call', new=AsyncMock(return_value=review())) as model:
            with self.assertRaises(g.PersonaDifferenceError):
                await g.review_document({}, TEXT, CRITERIA, 'keyword_stuffer', PLAN, None)
            model.assert_awaited_once()  # Genuine content mismatch goes back to generation.

    async def test_provider_outage_stops_without_consuming_review_repairs(self):
        with patch.object(g, 'model_call', new=AsyncMock(side_effect=ValueError('MODEL_UNAVAILABLE'))) as model:
            with self.assertRaisesRegex(ValueError, 'MODEL_UNAVAILABLE'):
                await g.review_document({}, TEXT, CRITERIA, 'strong_all_rounder', PLAN, None)
            model.assert_awaited_once()


class SuiteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, *, exhaust_keyword_review=False):
        from dataclasses import asdict
        execution = ModelExecution('gemini', 'fixture')
        profile = {'criteria': [{**CRITERIA[0], 'source_quote': 'Python experience.', 'priority_quote': 'Must have: Python experience.'}],
            'addressed_dimensions': ['technical_skills', 'work_experience', 'qualifications', 'additional_criteria'],
            'dimension_weights': {'technical_skills': 100, 'work_experience': 0, 'qualifications': 0, 'additional_criteria': 0},
            'weights_confirmed': True, 'weights_confirmation_quote': 'Technical skills 100%',
            'minimum_years_experience': None, 'fresher_eligibility': None, 'google_form_url': None}
        run = NS(id=uuid4(), versions={'model': 'fixture', 'generation': {'suite': g.SUITE_VERSION, 'persona_keys': list(g.PERSONAS),
            'persona_count': 2, 'expectations': g.EXPECTATION_VERSION, 'generator': g.GENERATOR_VERSION,
            'validator': g.VALIDATOR_VERSION, 'renderer': g.RENDERER_VERSION, 'execution': asdict(execution)}},
            snapshot={'content': '# Backend Developer\nBuild Python services.', 'screening_profile': profile, 'profile_version': 1}, resources={})
        rows, paths, calls, failures = [], {}, [], []
        repair_keyword = False
        db = NS(commit=AsyncMock(), scalars=AsyncMock(side_effect=lambda query: list(rows)))

        @asynccontextmanager
        async def factory():
            yield db

        with TemporaryDirectory() as directory:
            async def store(db, run_id, owner, persona, data, suffix, **kwargs):
                row = NS(id=uuid4(), file_sha256='fixture-hash', persona_id=persona)
                path = Path(directory) / f'{row.id}.docx'; path.write_bytes(data)
                paths[row.id] = path; rows.append(row)
                return row

            async def document(db, run_id, document_id, owner, **kwargs):
                return None, None, paths[document_id]

            async def model(prompt, payload, schema, execution):
                persona = payload['persona']
                calls.append(('generate' if prompt == g.GENERATE_PROMPT else 'review', persona))
                if prompt == g.GENERATE_PROMPT:
                    value = draft()
                    if persona != 'strong_all_rounder':
                        value['sections'][1]['paragraphs'] = ['Maintained internal software services and collaborated on delivery for a fictional team without named technologies.']
                    if persona == 'keyword_stuffer':
                        value['sections'].append({'heading': 'Skills', 'paragraphs': ['Python']})
                    return value
                support = 'demonstrated' if persona == 'strong_all_rounder' else 'listed' if persona == 'keyword_stuffer' else 'absent'
                ids = [p['id'] for p in payload['cv_passages'] if 'Built Python' in p['text']] if support == 'demonstrated' else [p['id'] for p in payload['cv_passages'] if p['text'] == 'Python'] if support == 'listed' else []
                value = review(support)
                value['criteria'][0]['evidence_ids'] = ids
                if (exhaust_keyword_review and persona == 'keyword_stuffer' and not repair_keyword) or (not exhaust_keyword_review and persona == 'keyword_stuffer' and not failures):
                    value['criteria'][0]['quotes'] = ['not an exact source quote']
                    failures.append(persona)
                return value

            with patch.object(g.storage, 'owned_run', new=AsyncMock(return_value=run)), \
                 patch.object(g.storage, 'store_document', new=AsyncMock(side_effect=store)), \
                 patch.object(g.storage, 'owned_document', new=AsyncMock(side_effect=document)), \
                 patch.object(g, 'model_version', new=AsyncMock(return_value={'model': 'fixture'})), \
                 patch.object(g, 'model_call', new=AsyncMock(side_effect=model)):
                result = await g.generate_suite(run.id, uuid4(), root=directory, session_factory=factory)
                if exhaust_keyword_review:
                    self.assertEqual(result['status'], 'failed')
                    self.assertEqual(set(result['documents']), {'strong_all_rounder'})
                    self.assertEqual(calls.count(('generate', 'keyword_stuffer')), 1)
                    repair_keyword = True
                before = {key: path.read_bytes() for key, path in paths.items()}
                result = await g.generate_suite(run.id, uuid4(), root=directory, session_factory=factory)
                self.assertEqual(result['status'], 'completed')
                self.assertEqual(len(result['documents']), 2)
                for key, data in before.items():
                    self.assertEqual(paths[key].read_bytes(), data)
                self.assertEqual(calls.count(('generate', 'strong_all_rounder')), 1)
                self.assertEqual(calls.count(('generate', 'keyword_stuffer')), 2 if exhaust_keyword_review else 1)
                self.assertEqual(calls.count(('generate', 'near_miss')), 0)
                self.assertTrue(result['review_attempts'])
                self.assertNotIn('not an exact source quote', str(result['review_attempts']))
                self.assertNotIn('previous_review', str(result['attempts']) if result.get('attempts') else '')

    async def test_bad_review_does_not_regenerate_valid_cv_and_replay_reuses_artifacts(self):
        await self.exercise()

    async def test_exhausted_review_retains_other_personas_and_explicit_retry_recovers(self):
        await self.exercise(exhaust_keyword_review=True)


@unittest.skipUnless(os.environ.get('RUN_F11_MODEL_TESTS') == '1', 'Opt in to synthetic live generation/review')
class LiveGenerationTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_personas_with_passage_grounded_reviews(self):
        from app.integrations.cv_model import provider, model_name, context_tokens
        execution = ModelExecution(provider(), model_name(), context_tokens())
        criteria = [*CRITERIA, {'id': 'docker', 'text': 'Docker experience.', 'dimension': 'technical_skills', 'priority': 'Preferred'}]
        plan = deepcopy(PLAN); plan['personas']['keyword_stuffer']['criterion_ids'] = ['python', 'docker']
        baseline = None
        for number, persona in enumerate(g.PERSONAS, 1):
            previous, error, details, excerpts = None, None, [], []
            for attempt in range(g.MAX_ATTEMPTS):
                payload = {'jd': '# Backend Developer\nBuild Python services and package applications using Docker.', 'criteria': criteria,
                    'persona': persona, 'probe': {'criterion_id': 'python'} if persona == 'near_miss' else {'criterion_ids': ['python', 'docker']},
                    'expected_support_by_criterion': g.support_expectations(criteria, persona, plan), 'baseline': baseline,
                    'repair_error': error, 'repair_details': details, 'repair_evidence': excerpts, 'previous_draft': previous}
                raw = await g.model_call(g.GENERATE_PROMPT, payload, g.Draft.model_json_schema(), execution)
                try:
                    checked_draft = g.prepare_draft(raw)
                    extracted = extract_document('fixture.docx', g.render_docx(checked_draft, number))
                    checked_review = await g.review_document(payload, extracted.text, criteria, persona, plan, execution)
                    self.assertEqual(set(r['criterion_id'] for r in checked_review['criteria']), {'python', 'docker'})
                    if persona == 'strong_all_rounder': baseline = checked_draft
                    print(f'Live synthetic persona validated: {persona}', flush=True)
                    break
                except (g.CVValidationError, g.PersonaDifferenceError) as exc:
                    previous, error, details, excerpts = raw, str(exc), exc.details, exc.evidence
                    print(json.dumps({'persona':persona,'attempt':attempt+1,'error':error,'repair_details':details}),flush=True)
                    if attempt + 1 == g.MAX_ATTEMPTS: raise


if __name__ == '__main__':
    unittest.main()
