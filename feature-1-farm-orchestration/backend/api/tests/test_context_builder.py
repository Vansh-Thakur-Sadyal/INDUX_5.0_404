import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from backend.api.services.context_builder import build_agri_context


class AgronomyContextBuilderTests(unittest.TestCase):
    @patch("backend.api.services.context_builder._fetch_weather")
    def test_builds_current_season_gps_region_weather_and_sensor_context(self, fetch_weather):
        fetch_weather.return_value = {
            "local_time": "2026-11-03T08:30",
            "timezone": "Asia/Kolkata",
            "temperature_c": 27.5,
            "humidity_percent": 62,
            "forecast": [],
            "source": "Open-Meteo",
        }
        resolve_location = Mock(return_value="Gwalior, Madhya Pradesh")

        context = build_agri_context(
            "suggest alternatives",
            {"lat": 26.2183, "lon": 78.1828},
            {
                "nitrogen": 42,
                "phosphorus": 28,
                "potassium": 55,
                "soil_ph": 6.8,
                "irrigation": "Drip",
                "soil_type": "Loamy",
            },
            now=datetime(2026, 11, 3, tzinfo=timezone.utc),
            location_resolver=resolve_location,
        )

        self.assertEqual(context["current_date"], "2026-11-03")
        self.assertEqual(context["season"], "Rabi")
        self.assertEqual(context["region"], "Gwalior, Madhya Pradesh")
        self.assertEqual(context["location_source"], "browser GPS")
        self.assertEqual(context["readings"]["nitrogen"], 42)
        self.assertEqual(context["npk_unit"], "mg/kg")
        self.assertEqual(context["irrigation"], "Drip")
        resolve_location.assert_called_once_with(26.218, 78.183)
        fetch_weather.assert_called_once_with(26.2183, 78.1828)

    @patch("backend.api.services.context_builder.requests.get")
    def test_does_not_invent_coordinates_or_locality_when_location_is_missing(self, get):
        context = build_agri_context(
            "tell me crops",
            {},
            {},
            now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        )

        self.assertEqual(context["region"], "India (regional location unavailable)")
        self.assertEqual(context["location_source"], "not available")
        self.assertEqual(context["season"], "Rabi")
        self.assertIsNone(context["weather"])
        get.assert_not_called()

    @patch("backend.api.services.context_builder._fetch_weather")
    def test_named_location_is_geocoded_and_weather_date_sets_local_season(self, fetch_weather):
        fetch_weather.return_value = {
            "local_time": "2026-06-01T10:00",
            "timezone": "Asia/Kolkata",
            "temperature_c": 31,
            "humidity_percent": 70,
            "forecast": [],
            "source": "Open-Meteo",
        }
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [{
                "latitude": 26.2183,
                "longitude": 78.1828,
                "name": "Gwalior",
                "admin1": "Madhya Pradesh",
                "country": "India",
            }]
        }
        with patch(
            "backend.api.services.context_builder.requests.get",
            return_value=response,
        ) as get:
            context = build_agri_context(
                "suggest crops in Gwalior",
                {},
                {},
                now=datetime(2026, 11, 3, tzinfo=timezone.utc),
            )

        self.assertEqual(context["region"], "Gwalior, Madhya Pradesh, India")
        self.assertEqual(context["current_date"], "2026-06-01")
        self.assertEqual(context["season"], "Kharif")
        self.assertEqual(get.call_args.args[0], "https://geocoding-api.open-meteo.com/v1/search")
        fetch_weather.assert_called_once_with(26.2183, 78.1828)

    def test_recent_hardware_telemetry_replaces_older_cached_sensor_values(self):
        context = build_agri_context(
            "recommend crops",
            {},
            {
                "nitrogen": 20,
                "phosphorus": 20,
                "potassium": 20,
                "timestamp": "2026-11-02T00:00:00Z",
                "source": "HM-10 BLE Serial",
            },
            now=datetime(2026, 11, 3, 6, tzinfo=timezone.utc),
            sensor_telemetry={
                "nitrogen": 42,
                "phosphorus": 28,
                "potassium": 55,
                "ph": 6.8,
                "soil_moisture": 32,
                "temperature": 29,
                "ec": 1.4,
                "timestamp": "2026-11-03T05:00:00Z",
                "source": "HM-10 BLE Serial",
            },
        )

        self.assertEqual(context["readings"]["nitrogen"], 42)
        self.assertEqual(context["readings"]["soil_ph"], 6.8)
        self.assertEqual(context["readings"]["soil_moisture"], 32)
        self.assertIn("HM-10", context["reading_source"])

    def test_currently_entered_form_values_take_priority_over_hardware_cache(self):
        context = build_agri_context(
            "recommend crops",
            {},
            {
                "nitrogen": 80,
                "phosphorus": 70,
                "potassium": 90,
                "input_source": "currently entered dashboard/NPK form values",
            },
            now=datetime(2026, 11, 3, 6, tzinfo=timezone.utc),
            sensor_telemetry={
                "nitrogen": 42,
                "phosphorus": 28,
                "potassium": 55,
                "timestamp": "2026-11-03T05:00:00Z",
            },
        )

        self.assertEqual(context["readings"]["nitrogen"], 80)