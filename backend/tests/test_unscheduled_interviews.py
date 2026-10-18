"""Opt-in PostgreSQL checks on a temporary copy of the live session table."""

import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.core.config import get_settings
from app.models.interviews import InterviewSession


@unittest.skipUnless(os.environ.get("RUN_INTERVIEW_DB_TESTS") == "1", "Set RUN_INTERVIEW_DB_TESTS=1")
class UnscheduledInterviewDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(get_settings().database_url, connect_args={"connect_timeout": 5})
        self.addCleanup(self.engine.dispose)
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        self.transaction = self.connection.begin()
        self.addCleanup(self.transaction.rollback)
        self.connection.execute(text(
            "CREATE TEMP TABLE test_interview_sessions "
            "(LIKE interview_sessions INCLUDING ALL) ON COMMIT DROP"
        ))

    def insert(self, starts_at=None, ends_at=None, *, status=None):
        values = {
            "id": uuid4(), "application_id": uuid4(), "job_description_id": uuid4(),
            "recruiter_id": uuid4(), "room_name": f"test-{uuid4()}",
            "starts_at": starts_at, "ends_at": ends_at,
        }
        columns = list(values)
        if status is not None:
            values["status"] = status
            columns.append("status")
        query = (
            f"INSERT INTO test_interview_sessions ({', '.join(columns)}) "
            f"VALUES ({', '.join(':' + column for column in columns)}) "
            "RETURNING status, starts_at, ends_at, display_timezone"
        )
        return self.connection.execute(text(query), values).one()

    def test_new_unscheduled_session_defaults_to_ready(self):
        row = self.insert()
        self.assertEqual(row.status, "ready")
        self.assertIsNone(row.starts_at)
        self.assertIsNone(row.ends_at)
        self.assertIsNone(row.display_timezone)

    def test_valid_scheduled_times_and_existing_states_remain_supported(self):
        start = datetime(2026, 10, 14, 4, tzinfo=timezone.utc)
        for status in ("scheduled", "in_progress", "completed", "cancelled"):
            with self.subTest(status=status):
                row = self.insert(start, start + timedelta(hours=1), status=status)
                self.assertEqual(row.status, status)
                self.assertEqual(row.starts_at, start)

    def test_partial_equal_and_reversed_planned_times_are_rejected(self):
        start = datetime(2026, 10, 14, 4, tzinfo=timezone.utc)
        for first, last in ((start, None), (None, start), (start, start), (start, start - timedelta(minutes=1))):
            with self.subTest(starts_at=first, ends_at=last):
                with self.connection.begin_nested():
                    with self.assertRaises(IntegrityError) as caught:
                        self.insert(first, last)
                    self.assertEqual(caught.exception.orig.diag.constraint_name, "ck_interview_session_times")
                    # Roll back the savepoint after an expected constraint failure.
                    self.connection.get_nested_transaction().rollback()

    def test_invalid_business_state_is_rejected(self):
        with self.connection.begin_nested():
            with self.assertRaises(IntegrityError) as caught:
                self.insert(status="connecting")
            self.assertEqual(caught.exception.orig.diag.constraint_name, "ck_interview_session_status")
            self.connection.get_nested_transaction().rollback()

    def test_live_column_nullability_and_ready_default_match_model(self):
        columns = {c["name"]: c for c in inspect(self.connection).get_columns("interview_sessions")}
        for name in ("starts_at", "ends_at", "display_timezone"):
            self.assertTrue(columns[name]["nullable"])
            self.assertTrue(InterviewSession.__table__.c[name].nullable)
        self.assertIn("ready", columns["status"]["default"])
        self.assertEqual(InterviewSession.__table__.c.status.default.arg, "ready")


if __name__ == "__main__":
    unittest.main()
