"""A code-only checkout must not require synthetic sample files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.services.interviews import assessment_runs, consolidation_runs


class OptionalSampleTests(unittest.TestCase):
    def test_demo_requires_all_files(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            with patch.object(assessment_runs, "FIXTURE", fixture):
                self.assertFalse(assessment_runs.synthetic_demo_available())
                for name in ("transcript.json", "timeline-ground-truth.json", "accuracy-references.json"):
                    (fixture / name).write_text("{}", encoding="utf-8")
                self.assertFalse(assessment_runs.synthetic_demo_available())
                (fixture / "cv-screening.json").write_text("{}", encoding="utf-8")
                self.assertTrue(assessment_runs.synthetic_demo_available())

    def test_missing_synthetic_cv_is_a_controlled_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(consolidation_runs, "FIXTURE", Path(directory)):
                with self.assertRaises(HTTPException) as caught:
                    consolidation_runs.synthetic_cv(None)
        self.assertEqual(caught.exception.status_code, 409)
        self.assertIn("not installed", caught.exception.detail)
