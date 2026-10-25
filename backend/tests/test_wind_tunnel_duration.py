"""Invented six-criterion duration probe; no saved jobs are loaded or dispatched."""
import json
import os
import unittest
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.integrations.cv_model import ModelExecution, provider, model_name, context_tokens
from app.services.documents.extraction import extract_document
from app.services.wind_tunnel import generation as g


REQUIREMENTS = [
    ('duration', 'At least two years of professional backend development experience.', 'work_experience'),
    ('api', 'Build REST APIs using Python and FastAPI.', 'technical_skills'),
    ('postgres', 'PostgreSQL schema design and query optimization.', 'technical_skills'),
    ('delivery', 'Use Docker and CI/CD to deliver services.', 'technical_skills'),
    ('tests', 'Write unit and integration tests.', 'technical_skills'),
    ('redis', 'Use Redis for caching.', 'technical_skills'),
]
CRITERIA = [dict(id=i, text=t, dimension=d, priority='Must have', source_quote=t,
                 priority_quote='Must have: ' + t) for i, t, d in REQUIREMENTS]
PROFILE = dict(criteria=CRITERIA, addressed_dimensions=['technical_skills', 'work_experience', 'qualifications', 'additional_criteria'],
    dimension_weights=dict(technical_skills=80, work_experience=20, qualifications=0, additional_criteria=0),
    weights_confirmed=True, weights_confirmation_quote='Technical skills 80%, work experience 20%',
    minimum_years_experience='2', fresher_eligibility=None, google_form_url=None)
JD = '# Backend developer\nMust have:\n' + '\n'.join(t for _, t, _ in REQUIREMENTS)
SNAPSHOT = dict(content=JD, screening_profile=PROFILE, profile_version=1)
BASELINE = {'sections': [
    {'heading': 'Summary', 'paragraphs': ['Backend developer delivering internal software for a fictional unnamed team.']},
    {'heading': 'Experience', 'paragraphs': [
        'Professional backend developer, January 2023 to December 2025, three years of paid backend development for an unnamed fictional employer.',
        'Built REST APIs in Python using FastAPI for an internal inventory service, implementing request validation and stock update endpoints.',
        'Designed the PostgreSQL schema for inventory records and optimized PostgreSQL queries with composite indexes and EXPLAIN plans to remove full-table scans.',
        'Packaged the inventory service in Docker images and built a CI/CD pipeline that tested images and deployed the service after checks passed.',
        'Wrote unit tests for stock calculations and integration tests for API requests against a test database, including rollback and validation cases.',
        'Implemented Redis caching for inventory lookups with invalidation on stock updates to prevent stale responses.',
    ]},
]}


def extracted(draft):
    return extract_document('invented-duration.docx', g.render_docx(draft, 3)).text


class TwoPersonaContractTests(unittest.TestCase):
    def test_only_two_personas_and_independent_support_expectations(self):
        plan = g.plan_suite(SNAPSHOT)
        self.assertEqual(g.PERSONAS, ('strong_all_rounder', 'keyword_stuffer'))
        self.assertEqual(set(plan['personas']), set(g.PERSONAS))
        self.assertNotIn('near_miss', plan['personas'])
        self.assertEqual(set(g.support_expectations(CRITERIA, 'strong_all_rounder', plan).values()), {'demonstrated'})
        keyword = g.support_expectations(CRITERIA, 'keyword_stuffer', plan)
        self.assertEqual(keyword['duration'], 'demonstrated')
        self.assertTrue(all(keyword[c['id']] == 'listed' for c in CRITERIA if c['dimension'] == 'technical_skills'))

    def test_baseline_actions_survive_real_docx_render_and_extract(self):
        text = extracted(BASELINE)
        for section in BASELINE['sections']:
            for paragraph in section['paragraphs']:
                self.assertIn(paragraph, text)

    def test_nontechnical_profile_has_no_keyword_probe(self):
        snapshot = deepcopy(SNAPSHOT)
        snapshot['screening_profile']['criteria'] = [deepcopy(CRITERIA[0])]
        snapshot['screening_profile']['dimension_weights'].update(technical_skills=0, work_experience=100)
        plan = g.plan_suite(snapshot)
        self.assertEqual(plan['personas']['keyword_stuffer']['criterion_ids'], [])
        self.assertEqual(plan['personas']['keyword_stuffer']['preserved_criterion_ids'], ['duration'])

    def test_saved_persona_counts_and_legacy_repetitions_are_preserved(self):
        from app.services.wind_tunnel.policy import personas_for, repetitions_for
        self.assertEqual(personas_for({}), ('strong_all_rounder', 'keyword_stuffer', 'near_miss'))
        self.assertEqual(repetitions_for({}), 3)
        self.assertEqual(personas_for({'generation': {'persona_keys': list(g.PERSONAS), 'persona_count': 2}}), g.PERSONAS)
        with self.assertRaises(ValueError):
            personas_for({'generation': {'persona_keys': list(g.PERSONAS), 'persona_count': 3}})
        with self.assertRaises(ValueError):
            personas_for({'generation': {'persona_keys': ['invented_role']}})

    def test_evaluation_uses_two_current_personas_and_three_only_for_historical_plans(self):
        from app.services.cv.scoring import RUBRIC, calculate_scores, validate_scoring_profile
        from app.services.wind_tunnel.evaluation import evaluate
        snapshot = {**SNAPSHOT, 'rubric': RUBRIC}
        plan = g.plan_suite(snapshot)
        def assessment(persona):
            matches = [dict(requirement_id=c['id'], decision='met', evidence=[{'fixture': True}], justification='Fixture support.') for c in CRITERIA]
            return dict(assessment_id=str(uuid4()), document_id=str(uuid4()), persona=persona, repetition=1,
                        status='completed', matches=matches, score=calculate_scores(validate_scoring_profile(PROFILE, 1), matches))
        rows = [assessment(p) for p in g.PERSONAS]
        report, _ = evaluate(snapshot, plan, rows)
        self.assertEqual(report['coverage'], dict(expected_assessments=2, completed_assessments=2, complete_cohorts=1))
        self.assertEqual(set(report['personas']), set(g.PERSONAS))
        legacy = deepcopy(plan)
        legacy['personas']['near_miss'] = dict(criterion_id='duration', essential_gap_applicable=True, review_flag_expected=True)
        report, _ = evaluate(snapshot, legacy, [*rows, assessment('near_miss')])
        self.assertEqual(report['coverage']['expected_assessments'], 3)
        self.assertEqual(report['coverage']['completed_assessments'], 3)
        self.assertEqual(len(g.PERSONAS), 2)


if __name__ == '__main__':
    unittest.main()
