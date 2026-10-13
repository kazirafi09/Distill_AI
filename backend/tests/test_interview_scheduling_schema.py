"""Scheduling input validation; no database or media connection required."""

import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app.services.interviews.schemas import ScheduleInterviewRequest


class ScheduleInterviewRequestTests(unittest.TestCase):
    def request(self, **changes):
        values = {
            "starts_at": "2026-10-14T10:00:00+06:00",
            "ends_at": "2026-10-14T11:00:00+06:00",
        }
        values.update(changes)
        return ScheduleInterviewRequest(**values)

    def test_local_times_are_normalized_to_utc(self):
        request = self.request()
        self.assertEqual(request.starts_at, datetime(2026, 10, 14, 4, tzinfo=timezone.utc))
        self.assertEqual(request.ends_at, datetime(2026, 10, 14, 5, tzinfo=timezone.utc))
        self.assertIs(request.starts_at.tzinfo, timezone.utc)
        self.assertEqual(request.display_timezone, "Asia/Dhaka")

    def test_utc_input_and_alternative_display_timezone_are_valid(self):
        request = self.request(
            starts_at="2026-10-14T04:00:00Z",
            ends_at="2026-10-14T05:00:00Z",
            display_timezone="America/New_York",
        )
        self.assertEqual(request.starts_at.hour, 4)
        self.assertEqual(request.display_timezone, "America/New_York")

    def test_naive_start_or_end_is_rejected(self):
        for field in ("starts_at", "ends_at"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                self.request(**{field: "2026-10-14T10:00:00"})

    def test_equal_and_reversed_times_are_rejected(self):
        for end in ("2026-10-14T10:00:00+06:00", "2026-10-14T09:59:00+06:00"):
            with self.subTest(end=end), self.assertRaisesRegex(ValidationError, "end must be after"):
                self.request(ends_at=end)

    def test_order_uses_actual_instants_across_offsets(self):
        # End looks later on a wall clock, but is earlier in UTC.
        with self.assertRaisesRegex(ValidationError, "end must be after"):
            self.request(starts_at="2026-10-14T10:00:00Z", ends_at="2026-10-14T11:00:00+06:00")
        request = self.request(
            starts_at="2026-10-14T10:00:00+06:00",
            ends_at="2026-10-14T05:00:00Z",
        )
        self.assertGreater(request.ends_at, request.starts_at)

    def test_invalid_timezone_keys_are_rejected(self):
        for key in ("Unknown/Timezone", "", "/Asia/Dhaka", "../Asia/Dhaka"):
            with self.subTest(key=key), self.assertRaisesRegex(ValidationError, "valid timezone"):
                self.request(display_timezone=key)

if __name__ == "__main__":
    unittest.main()
