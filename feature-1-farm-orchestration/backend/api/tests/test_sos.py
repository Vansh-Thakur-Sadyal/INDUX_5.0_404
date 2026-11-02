import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend.api import sos


class SosApiTests(unittest.TestCase):
    def setUp(self):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        database_path = Path(temporary_directory.name) / "sos.sqlite3"
        database_patcher = patch.object(sos, "DATABASE_PATH", database_path)
        database_patcher.start()
        self.addCleanup(database_patcher.stop)

    @staticmethod
    def report_payload(**overrides):
        payload = {
            "incident_type": "Fire",
            "description": "Smoke near the east field gate.",
            "latitude": 26.2,
            "longitude": 78.2,
            "share_consent": True,
            "audio_type": "audio/webm;codecs=opus",
            "audio_base64": base64.b64encode(b"sample voice note").decode("ascii"),
        }
        payload.update(overrides)
        return payload

    def create_report(self):
        return sos.create_sos_report(self.report_payload())

    def test_create_and_list_report_only_within_five_kilometers(self):
        created = self.create_report()

        nearby = sos.get_nearby_sos_reports({"latitude": 26.21, "longitude": 78.2})
        distant = sos.get_nearby_sos_reports({"latitude": 26.3, "longitude": 78.2})

        self.assertEqual(created["status"], "success")
        self.assertEqual(len(nearby["reports"]), 1)
        self.assertEqual(nearby["radius_km"], 5)
        self.assertEqual(nearby["reports"][0]["id"], created["incident_id"])
        self.assertEqual(nearby["reports"][0]["latitude"], 26.2)
        self.assertTrue(nearby["reports"][0]["audio_url"].endswith("/audio"))
        self.assertEqual(distant["reports"], [])

    def test_audio_playback_requires_viewer_within_five_kilometers(self):
        created = self.create_report()
        response = sos.play_sos_audio(
            created["incident_id"],
            {"latitude": 26.21, "longitude": 78.2},
        )

        self.assertEqual(response.body, b"sample voice note")
        self.assertEqual(response.media_type, "audio/webm")
        with self.assertRaises(HTTPException) as error:
            sos.play_sos_audio(
                created["incident_id"],
                {"latitude": 26.3, "longitude": 78.2},
            )
        self.assertEqual(error.exception.status_code, 403)

    def test_reaction_is_upserted_once_per_device(self):
        created = self.create_report()
        reaction = {
            "latitude": 26.21,
            "longitude": 78.2,
            "device_id": "test-device-identifier-001",
            "reaction": "seen",
        }
        first = sos.react_to_sos_report(created["incident_id"], reaction)
        second = sos.react_to_sos_report(
            created["incident_id"],
            {**reaction, "reaction": "can_help"},
        )

        self.assertEqual(first["reactions"], {"seen": 1})
        self.assertEqual(second["reactions"], {"can_help": 1})

    def test_expired_reports_are_removed_and_cannot_be_played(self):
        created = self.create_report()
        expired_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with sos._connection() as connection:
            connection.execute(
                "UPDATE incidents SET expires_at = ? WHERE id = ?",
                (expired_at, created["incident_id"]),
            )

        feed = sos.get_nearby_sos_reports({"latitude": 26.2, "longitude": 78.2})

        self.assertEqual(feed["reports"], [])
        with self.assertRaises(HTTPException) as error:
            sos.play_sos_audio(
                created["incident_id"],
                {"latitude": 26.2, "longitude": 78.2},
            )
        self.assertEqual(error.exception.status_code, 404)

    def test_voice_note_and_exact_coordinates_are_required(self):
        with self.assertRaises(HTTPException) as no_audio:
            sos.create_sos_report(self.report_payload(audio_base64=""))
        self.assertEqual(no_audio.exception.status_code, 422)

        with self.assertRaises(HTTPException) as no_consent:
            sos.create_sos_report(self.report_payload(share_consent=False))
        self.assertEqual(no_consent.exception.status_code, 422)

        with self.assertRaises(HTTPException) as invalid_location:
            sos.create_sos_report(self.report_payload(latitude=91))
        self.assertEqual(invalid_location.exception.status_code, 422)

    def test_audio_size_limit_is_enforced(self):
        oversized = base64.b64encode(b"x" * (sos.MAX_AUDIO_BYTES + 1)).decode("ascii")
        with self.assertRaises(HTTPException) as error:
            sos.create_sos_report(self.report_payload(audio_base64=oversized))
        self.assertEqual(error.exception.status_code, 413)


if __name__ == "__main__":
    unittest.main()
