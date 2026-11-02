import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from backend.api import hardware_share as hardware


class HardwareShareTests(unittest.TestCase):
    def setUp(self):
        hardware.reset_state_for_tests()
        self.now = datetime(2026, 11, 3, 0, 0, tzinfo=timezone.utc)
        patcher = patch.object(hardware, "_utcnow", return_value=self.now)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        hardware.reset_state_for_tests()

    @staticmethod
    def join_body(**overrides):
        body = hardware.QueueJoinRequest(
            hardware_id=hardware.SAMPLE_UNIT_ID,
            user_lat=hardware.SAMPLE_HUB_LATITUDE,
            user_lng=hardware.SAMPLE_HUB_LONGITUDE,
        )
        return HardwareShareTests.update_model(body, overrides)

    @staticmethod
    def start_body(**overrides):
        body = hardware.SessionStartRequest(
            hardware_id=hardware.SAMPLE_UNIT_ID,
            start_lat=hardware.SAMPLE_HUB_LATITUDE,
            start_lng=hardware.SAMPLE_HUB_LONGITUDE,
        )
        return HardwareShareTests.update_model(body, overrides)

    @staticmethod
    def update_model(body, overrides):
        model_copy = getattr(body, "model_copy", None)
        if model_copy:
            return model_copy(update=overrides)
        return body.copy(update=overrides)

    def join(self, user_id, body=None):
        return hardware.join_hardware_queue(
            body or self.join_body(),
            user_id=user_id,
        )

    def start(self, user_id, body=None):
        return hardware.start_hardware_session(
            body or self.start_body(),
            user_id=user_id,
        )["session"]

    def return_session(self, user_id, session, **overrides):
        body = hardware.SessionReturnRequest(
            session_id=session["id"],
            session_token=session["session_token"],
            end_lat=hardware.SAMPLE_HUB_LATITUDE,
            end_lng=hardware.SAMPLE_HUB_LONGITUDE,
        )
        body = self.update_model(body, overrides)
        return hardware.return_hardware_session(body, user_id=user_id)

    def test_seeded_unit_is_listed_as_demo_and_available(self):
        result = hardware.list_hardware_units()

        self.assertEqual(len(result["units"]), 1)
        self.assertEqual(result["units"][0]["id"], hardware.SAMPLE_UNIT_ID)
        self.assertEqual(result["units"][0]["status"], "AVAILABLE")
        self.assertTrue(result["units"][0]["is_sample"])
        self.assertIn("in-memory", result["storage"])

    def test_queue_allocates_first_user_and_promotes_next_after_leave(self):
        first = self.join("farmer-1")["queue"]
        second = self.join("farmer-2")["queue"]

        self.assertEqual((first["queue_position"], first["status"]), (1, "ALLOCATED"))
        self.assertEqual((second["queue_position"], second["status"]), (2, "WAITING"))

        hardware.leave_hardware_queue(
            hardware.QueueLeaveRequest(queue_id=first["id"]),
            user_id="farmer-1",
        )
        active = hardware._active_queue_for_unit(hardware.SAMPLE_UNIT_ID)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].user_id, "farmer-2")
        self.assertEqual(active[0].status, hardware.QueueStatus.ALLOCATED)
        self.assertEqual(active[0].queue_position, 1)

    def test_queue_status_endpoint_updates_waiting_user_and_checks_owner(self):
        first = self.join("farmer-1")["queue"]
        second = self.join("farmer-2")["queue"]

        with self.assertRaises(HTTPException) as forbidden:
            hardware.get_hardware_queue_entry(second["id"], user_id="farmer-3")
        self.assertEqual(forbidden.exception.status_code, 403)

        hardware.leave_hardware_queue(
            hardware.QueueLeaveRequest(queue_id=first["id"]),
            user_id="farmer-1",
        )
        promoted = hardware.get_hardware_queue_entry(second["id"], user_id="farmer-2")
        self.assertEqual(promoted["queue"]["status"], "ALLOCATED")
        self.assertEqual(promoted["queue"]["queue_position"], 1)

    def test_distance_is_not_enforced_and_duplicate_waiting_entry_is_rejected(self):
        far_away = self.join_body(user_lat=26.4)
        accepted = self.join("farmer-1", far_away)
        self.assertEqual(accepted["queue"]["status"], "ALLOCATED")
        self.assertNotIn("distance_km", accepted)

        with self.assertRaises(HTTPException) as duplicate:
            self.join("farmer-1")
        self.assertEqual(duplicate.exception.status_code, 409)

    def test_session_requires_allocated_head_but_does_not_enforce_distance(self):
        self.join("farmer-1")
        queued = self.join("farmer-2")["queue"]
        with self.assertRaises(HTTPException) as not_allocated:
            self.start("farmer-2")
        self.assertEqual(not_allocated.exception.status_code, 409)

        distant_start = self.start_body(start_lat=27.0, start_lng=79.0)
        session = self.start("farmer-1", distant_start)
        result = self.return_session(
            "farmer-1",
            session,
            end_lat=27.1,
            end_lng=79.1,
        )

        self.assertEqual(result["session"]["status"], "COMPLETED")
        self.assertEqual(hardware._sessions[session["id"]].start_latitude, 27.0)
        self.assertEqual(hardware._sessions[session["id"]].end_latitude, 27.1)
        self.assertEqual(result["next_queue"]["id"], queued["id"])
        self.assertEqual(
            hardware._units[hardware.SAMPLE_UNIT_ID].status,
            hardware.UnitStatus.AVAILABLE,
        )
        self.assertEqual(
            hardware._queue[queued["id"]].status,
            hardware.QueueStatus.ALLOCATED,
        )

    def test_join_start_and_return_allow_missing_coordinates(self):
        self.join("farmer-1", self.join_body(user_lat=None, user_lng=None))
        session = self.start(
            "farmer-1",
            self.start_body(start_lat=None, start_lng=None),
        )
        result = self.return_session(
            "farmer-1",
            session,
            end_lat=None,
            end_lng=None,
        )

        self.assertEqual(result["session"]["status"], "COMPLETED")
        self.assertIsNone(hardware._sessions[session["id"]].start_latitude)
        self.assertIsNone(hardware._sessions[session["id"]].end_latitude)

    def test_return_requires_owner_and_session_token(self):
        self.join("farmer-1")
        session = self.start("farmer-1")

        with self.assertRaises(HTTPException) as wrong_user:
            self.return_session("farmer-2", session)
        self.assertEqual(wrong_user.exception.status_code, 403)

        with self.assertRaises(HTTPException) as wrong_token:
            self.return_session(
                "farmer-1",
                session,
                session_token="x" * len(session["session_token"]),
            )
        self.assertEqual(wrong_token.exception.status_code, 403)

    def test_two_started_sessions_per_calendar_month_are_allowed(self):
        for _ in range(2):
            self.join("farmer-1")
            session = self.start("farmer-1")
            self.return_session("farmer-1", session)

        with self.assertRaises(HTTPException) as limit:
            self.join("farmer-1")
        self.assertEqual(limit.exception.status_code, 429)

    def test_overdue_worker_penalizes_once_and_keeps_unit_in_use_until_return(self):
        self.join("farmer-1")
        session = self.start("farmer-1")
        expired_at = datetime.fromisoformat(session["expires_at"])
        after_expiry = expired_at + timedelta(seconds=1)

        self.assertEqual(hardware.process_overdue_sessions(after_expiry), 1)
        self.assertEqual(hardware.process_overdue_sessions(after_expiry), 0)
        self.assertEqual(
            hardware._sessions[session["id"]].status,
            hardware.SessionStatus.OVERDUE,
        )
        self.assertEqual(
            hardware._blacklist["farmer-1"].blacklisted_until,
            after_expiry + timedelta(days=7),
        )
        self.assertEqual(
            hardware._units[hardware.SAMPLE_UNIT_ID].status,
            hardware.UnitStatus.IN_USE,
        )
        with self.assertRaises(HTTPException) as blacklisted:
            self.join("farmer-1")
        self.assertEqual(blacklisted.exception.status_code, 403)

        with patch.object(hardware, "_utcnow", return_value=after_expiry):
            result = self.return_session("farmer-1", session)
        self.assertEqual(result["session"]["status"], "OVERDUE")
        self.assertEqual(
            hardware._units[hardware.SAMPLE_UNIT_ID].status,
            hardware.UnitStatus.AVAILABLE,
        )

    def test_returning_late_without_worker_also_blacklists(self):
        self.join("farmer-1")
        session = self.start("farmer-1")
        late_time = datetime.fromisoformat(session["expires_at"]) + timedelta(seconds=1)

        with patch.object(hardware, "_utcnow", return_value=late_time):
            result = self.return_session("farmer-1", session)

        self.assertEqual(result["session"]["status"], "OVERDUE")
        self.assertEqual(
            result["session"]["blacklisted_until"],
            (late_time + timedelta(days=7)).isoformat(),
        )


if __name__ == "__main__":
    unittest.main()
