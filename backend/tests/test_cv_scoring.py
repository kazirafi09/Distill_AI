import unittest
from types import SimpleNamespace
from decimal import Decimal
from app.services.cv_scoring import calculate_scores, rank_assessments, validate_scoring_profile
from app.services.cv_matching import validate_match


def profile():
    return {"criteria": [{"id": "python", "text": "Python", "dimension": "technical_skills", "priority": "Must have", "source_quote": "Python", "priority_quote": "Must have"},
        {"id": "rust", "text": "Rust", "dimension": "technical_skills", "priority": "Preferred", "source_quote": "Rust", "priority_quote": "Preferred"}],
        "addressed_dimensions": ["technical_skills", "work_experience", "qualifications", "additional_criteria"],
        "minimum_years_experience": None, "fresher_eligibility": None,
        "dimension_weights": {"technical_skills": 100, "work_experience": 0, "qualifications": 0, "additional_criteria": 0},
        "weights_confirmed": True, "weights_confirmation_quote": "Technical skills 100%", "google_form_url": "https://forms.gle/synthetic"}


class ScoreTests(unittest.TestCase):
    def test_matching_cannot_add_an_unrequested_threshold(self):
        parsed = SimpleNamespace(id="parse", file_version_id="file", text="Python developer", sources=[{"id": "page", "start": 0, "end": 16, "method": "native"}])
        match = {"requirement_id": "python", "decision": "partially_met", "justification": "No proficiency level stated",
            "unsupported_requirement_parts": ["proficiency"], "evidence": [{"parse_id": "parse", "file_version_id": "file", "source_id": "page", "quote": "Python developer"}]}
        with self.assertRaises(ValueError): validate_match(match, profile()["criteria"][0], [parsed])
        match["unsupported_requirement_parts"] = ["Python"]
        with self.assertRaises(ValueError): validate_match(match, profile()["criteria"][0], [parsed])
        match["decision"], match["unsupported_requirement_parts"] = "met", []
        saved = validate_match(match, profile()["criteria"][0], [parsed])
        self.assertEqual(validate_match(saved, profile()["criteria"][0], [parsed]), saved)

    def test_arithmetic_and_ties(self):
        data = profile()
        matches = [{"requirement_id": "python", "decision": "partially_met", "justification": "Some support", "evidence": ["fixture"]},
            {"requirement_id": "rust", "decision": "insufficient_evidence", "justification": "Not stated", "evidence": []}]
        score = calculate_scores(validate_scoring_profile(data, 1), matches)
        self.assertAlmostEqual(float(score["aggregate"]), 100 / 3)
        self.assertEqual(score["must_have_review_flags"], ["python"])
        self.assertEqual(score["evidence_coverage"], "0.5")
        better = {**score, "aggregate": "80"}
        ranked = rank_assessments([{"application_id": "b", "score": score}, {"application_id": "a", "score": score}, {"application_id": "c", "score": better}])
        self.assertEqual([(v["application_id"], v["rank"]) for v in ranked], [("c", 1), ("a", 2), ("b", 2)])
        # Form identity metadata never enters approved criteria or scoring.
        validated = validate_scoring_profile(data, 1)
        self.assertEqual(calculate_scores({**validated, "sheet_email": "uploader@gmail.com", "cv_email": "candidate@example.com"}, matches), score)

    def test_invalid_weights_and_empty_dimension(self):
        data = profile()
        data["dimension_weights"]["technical_skills"] = 90
        with self.assertRaises(ValueError): validate_scoring_profile(data, 1)
        data["dimension_weights"]["work_experience"] = 10
        with self.assertRaises(ValueError): validate_scoring_profile(data, 1)
        with self.assertRaises(ValueError): calculate_scores(profile(), [])
