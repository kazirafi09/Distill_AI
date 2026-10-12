"""Check shortlist filters using sample screening results."""

import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic import ValidationError

from app.services.interviews.shortlisting import (
    ShortlistRequest,
    preview_shortlist,
)


class ShortlistingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.job_id = uuid4()
        self.recruiter_id = uuid4()

    def application(self, application_id, score):
        return {
            "application_id": application_id,
            "score": {"aggregate": score},
            "status": "completed",
            "outdated": False,
        }

    async def preview(self, ranked, filters, incomplete=None):
        results = {
            "job_id": str(self.job_id),
            "run": {"id": "test-run"},
            "ranked": ranked,
            "incomplete": incomplete or [],
            "warnings": [],
        }

        with patch(
            "app.services.interviews.shortlisting.get_screening_results",
            new_callable=AsyncMock,
            return_value=results,
        ) as screening:
            response = await preview_shortlist(
                self.job_id,
                self.recruiter_id,
                ShortlistRequest(**filters),
            )

        screening.assert_awaited_once_with(
            self.job_id,
            self.recruiter_id,
        )
        self.assertEqual(response["job_id"], str(self.job_id))
        self.assertEqual(response["run"], results["run"])
        self.assertEqual(
            response["proposed_count"],
            len(response["proposed"]),
        )
        self.assertEqual(response["warnings"], results["warnings"])
        return response

    async def test_three_filter_modes(self):
        ranked = [
            self.application("a", "92"),
            self.application("b", "86"),
            self.application("c", "81"),
            self.application("d", "74"),
        ]
        cases = [
            ({"mode": "top_k", "k": 2}, ["a", "b"]),
            (
                {"mode": "minimum_score", "minimum_score": 81},
                ["a", "b", "c"],
            ),
            (
                {"mode": "both", "k": 2, "minimum_score": 80},
                ["a", "b"],
            ),
        ]

        for filters, expected_ids in cases:
            with self.subTest(filters=filters):
                response = await self.preview(ranked, filters)
                actual_ids = [
                    item["application_id"]
                    for item in response["proposed"]
                ]
                self.assertEqual(actual_ids, expected_ids)

    async def test_zero_threshold_and_full_precision(self):
        ranked = [
            self.application("a", "80"),
            self.application("b", "79.9999"),
            self.application("c", "0"),
        ]

        response = await self.preview(
            ranked,
            {"mode": "minimum_score", "minimum_score": 80},
        )
        self.assertEqual(response["proposed"], ranked[:1])

        response = await self.preview(
            ranked,
            {"mode": "minimum_score", "minimum_score": 0},
        )
        self.assertEqual(response["proposed"], ranked)

    async def test_top_k_preserves_existing_order_for_ties(self):
        ranked = [
            self.application("a", "90"),
            self.application("b", "90"),
            self.application("c", "90"),
        ]

        response = await self.preview(
            ranked,
            {"mode": "top_k", "k": 2},
        )
        self.assertEqual(response["proposed"], ranked[:2])

    async def test_fewer_matches_than_k_does_not_fill_below_threshold(self):
        ranked = [
            self.application("a", "90"),
            self.application("b", "70"),
        ]

        response = await self.preview(
            ranked,
            {"mode": "both", "k": 5, "minimum_score": 80},
        )
        self.assertEqual(response["proposed"], ranked[:1])

    async def test_empty_results_and_no_matches(self):
        for ranked in ([], [self.application("a", "70")]):
            with self.subTest(ranked=ranked):
                response = await self.preview(
                    ranked,
                    {"mode": "minimum_score", "minimum_score": 80},
                )
                self.assertEqual(response["proposed"], [])
                self.assertEqual(response["proposed_count"], 0)

    async def test_exclusion_reasons(self):
        ranked = [
            self.application("a", "92"),
            self.application("b", "86"),
            self.application("c", "74"),
        ]
        incomplete = [
            {
                "application_id": "d",
                "status": "completed",
                "outdated": True,
            },
            {
                "application_id": "e",
                "status": "failed",
                "outdated": False,
            },
            {
                "application_id": "f",
                "status": "completed",
                "outdated": False,
            },
        ]

        response = await self.preview(
            ranked,
            {"mode": "both", "k": 1, "minimum_score": 80},
            incomplete,
        )
        reasons = {
            item["application_id"]: item["exclusion_reason"]
            for item in response["excluded"]
        }
        self.assertEqual(reasons, {
            "b": "outside_top_k",
            "c": "below_minimum_score",
            "d": "outdated_assessment",
            "e": "assessment_not_completed",
            "f": "current_ranking_unavailable",
        })

    def test_invalid_filter_inputs(self):
        invalid_inputs = [
            {"mode": "unknown"},
            {"mode": "top_k"},
            {"mode": "top_k", "k": 0},
            {"mode": "top_k", "k": -1},
            {"mode": "top_k", "k": 1.5},
            {"mode": "top_k", "k": True},
            {"mode": "minimum_score"},
            {"mode": "minimum_score", "minimum_score": -1},
            {"mode": "minimum_score", "minimum_score": 101},
            {"mode": "minimum_score", "minimum_score": "NaN"},
            {"mode": "minimum_score", "minimum_score": "Infinity"},
            {"mode": "both", "k": 2},
            {"mode": "both", "minimum_score": 80},
        ]

        for filters in invalid_inputs:
            with self.subTest(filters=filters):
                with self.assertRaises(ValidationError):
                    ShortlistRequest(**filters)


if __name__ == "__main__":
    unittest.main()
