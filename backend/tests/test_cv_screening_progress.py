"""Progress uses persisted work, including partial matches and failed steps."""
import unittest
from types import SimpleNamespace

from app.services.cv.progress import screening_progress
from app.services.cv.screening import valid_aggregate_score


def assessment(status, *, matches=(), parsed=False, score=None):
    return SimpleNamespace(status=status, parse_result_ids=["saved-parse"] if parsed else [],
        matches=[{"requirement_id": key} for key in matches], score={"aggregate": score} if score is not None else None)


class ScreeningProgressTests(unittest.TestCase):
    def progress(self, items, status="running"):
        run = SimpleNamespace(status=status, inputs={"profile": {"criteria": [{"id": "a"}, {"id": "b"}]}})
        return screening_progress(run, items, valid_score=valid_aggregate_score)

    def test_read_match_and_score_counts_come_from_saved_state(self):
        result = self.progress([assessment("parsing"), assessment("matching", parsed=True, matches=["a"]),
            assessment("completed", parsed=True, matches=["a", "b"], score="0")])
        self.assertEqual(result["stage"], "parsing")
        self.assertEqual(result["parsed"], 2)
        self.assertEqual(result["criteria_completed"], 3)
        self.assertEqual(result["criteria_total"], 6)
        self.assertEqual(result["scored"], 1)
        self.assertEqual(result["completed_units"], 6)
        self.assertEqual(result["total_units"], 12)

    def test_failed_steps_are_processed_but_not_claimed_as_successful(self):
        result = self.progress([assessment("failed", parsed=True, matches=["a"])], "completed_with_issues")
        self.assertEqual(result["parsed"], 1)
        self.assertEqual(result["criteria_completed"], 1)
        self.assertEqual(result["scored"], 0)
        self.assertEqual(result["criteria_skipped"], 1)
        self.assertEqual(result["score_skipped"], 1)
        self.assertEqual(result["completed_units"], result["total_units"])

    def test_failed_parsing_does_not_become_a_parsed_cv(self):
        result = self.progress([assessment("needs_review")])
        self.assertEqual(result["parsed"], 0)
        self.assertEqual(result["read_skipped"], 1)
        self.assertEqual(result["skipped_units"], 4)

    def test_duplicate_unknown_matches_and_invalid_scores_do_not_add_progress(self):
        result = self.progress([assessment("scoring", parsed=True, matches=["a", "a", "foreign"]),
            assessment("completed", score="NaN")])
        self.assertEqual(result["criteria_completed"], 1)
        self.assertEqual(result["scored"], 0)

    def test_all_saved_checks_can_be_complete_while_ranking_is_finishing(self):
        result = self.progress([assessment("completed", parsed=True, matches=["a", "b"], score="80")])
        self.assertEqual(result["stage"], "ranking")
        self.assertEqual(result["completed_units"], result["total_units"])


if __name__ == "__main__":
    unittest.main()
