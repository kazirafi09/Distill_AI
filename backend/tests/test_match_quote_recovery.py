"""Quote recovery uses exact source passages; all model calls are mocked."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from app.services.cv import matching as m


def source(text='Projects\nPersonal inventory project: built an inventory tracker.\n'):
    return NS(id='parse-1', file_version_id='file-1', text=text,
              sources=[dict(id='page-1', start=0, end=len(text), method='native')], profile={})


def result(evidence):
    return dict(requirement_id='criterion-1', decision='met', justification='The CV states a personal project.',
                unsupported_requirement_parts=[], evidence=evidence)


class QuoteCatalogTests(unittest.TestCase):
    def test_source_bounds_unicode_and_long_lines_remain_exact(self):
        text = 'Untrusted prefix\n' + ('word ' * 400) + '\nCafe\u0301 project — a real passage.\nUntrusted suffix'
        parsed = source(text)
        parsed.sources[0].update(start=len('Untrusted prefix\n'), end=text.index('Untrusted suffix'))
        catalog = m.excerpt_catalog([parsed])
        self.assertGreater(len(catalog), 2)
        self.assertNotIn('Untrusted prefix', str(catalog))
        self.assertNotIn('Untrusted suffix', str(catalog))
        for identifier, excerpt in catalog.items():
            self.assertLessEqual(len(excerpt['quote']), 1500)
            resolved = m.resolve_selected_match(result([dict(excerpt_id=identifier)]), catalog)
            match = m.validate_match(resolved, dict(id='criterion-1', text='Personal projects'), [parsed])
            evidence = match['evidence'][0]
            self.assertEqual(text[evidence['start']:evidence['end']], evidence['quote'])
            self.assertGreaterEqual(evidence['start'], parsed.sources[0]['start'])
            self.assertLessEqual(evidence['end'], parsed.sources[0]['end'])

    def test_unknown_ids_and_fabricated_quotes_are_rejected(self):
        catalog = m.excerpt_catalog([source()])
        with self.assertRaisesRegex(ValueError, 'MATCH_SOURCE_INVALID'):
            m.resolve_selected_match(result([dict(excerpt_id='foreign-cv')]), catalog)
        with self.assertRaises(ValueError):
            m.resolve_selected_match(result([dict(excerpt_id='e1', quote='invented')]), catalog)
        with self.assertRaisesRegex(ValueError, 'MATCH_SUPPORT_REQUIRED'):
            m.validate_match(m.resolve_selected_match(result([]), catalog), dict(id='criterion-1', text='Projects'), [source()])


class QuoteRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_quote_repairs_by_selecting_original_passage(self):
        parsed = source()
        invalid = result([dict(file_version_id='file-1', parse_id='parse-1', source_id='page-1',
                               quote='Built a production inventory service with deployment experience.')])
        model = AsyncMock(side_effect=[json.dumps(invalid), json.dumps(result([dict(excerpt_id='e2')]))])
        with patch.object(m, 'generate_json', model), patch.object(m, 'context_tokens', return_value=262144), \
             patch.object(m, 'get_settings', return_value=NS(cv_model_output_tokens=16384)):
            match, warnings = await m.match_criterion(dict(id='criterion-1', text='Personal projects'),
                [parsed], dict(evidence=[]), {})
        self.assertEqual(model.await_count, 2)
        self.assertEqual(match['decision'], 'met')
        evidence = match['evidence'][0]
        self.assertEqual(evidence['quote'], 'Personal inventory project: built an inventory tracker.')
        self.assertEqual(parsed.text[evidence['start']:evidence['end']], evidence['quote'])
        payload = json.loads(model.await_args_list[1].args[1])
        self.assertIn('verified_excerpt_catalog', payload)
        schema = model.await_args_list[1].args[2]
        self.assertEqual(schema['$defs']['EvidenceSelection']['properties']['excerpt_id']['enum'], ['e1', 'e2'])
        self.assertEqual(warnings, [])

    async def test_unknown_selections_exhaust_bounded_repair_without_scoring(self):
        invalid = result([dict(file_version_id='file-1', parse_id='parse-1', source_id='page-1', quote='invented quote')])
        unknown = result([dict(excerpt_id='unknown')])
        model = AsyncMock(side_effect=[json.dumps(invalid), json.dumps(unknown), json.dumps(unknown)])
        with patch.object(m, 'generate_json', model), patch.object(m, 'context_tokens', return_value=262144), \
             patch.object(m, 'get_settings', return_value=NS(cv_model_output_tokens=16384)):
            with self.assertRaises(m.MatchValidationFailure) as caught:
                await m.match_criterion(dict(id='criterion-1', text='Projects'), [source()], dict(evidence=[]), {})
        self.assertEqual(model.await_count, 3)
        self.assertEqual(caught.exception.validation_code, 'MATCH_SOURCE_INVALID')


if __name__ == '__main__':
    unittest.main()
