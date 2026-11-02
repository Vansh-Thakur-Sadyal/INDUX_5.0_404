import unittest

from backend.api.server import parse_hc05_text


ARDUINO_FRAME = (
    "N=22,P=45,K=38,TEMP_C=26.3,"
    "MOISTURE_PCT=62.1,EC_US_CM=185,PH=5.8\n"
)


class HardwareTelemetryTests(unittest.TestCase):
    def test_parses_arduino_key_value_frame(self):
        parsed = parse_hc05_text(ARDUINO_FRAME)

        self.assertEqual(parsed, {
            "nitrogen": 22.0,
            "phosphorus": 45.0,
            "potassium": 38.0,
            "temperature": 26.3,
            "soil_moisture": 62.1,
            "ec_us_cm": 185.0,
            "ph": 5.8,
            "ec_raw": 185.0,
            "ec": 0.185
        })

    def test_rejects_incomplete_arduino_frame(self):
        self.assertEqual(parse_hc05_text("N=22,P=45,K=38\n"), {})

    def test_rejects_out_of_range_sensor_frame(self):
        invalid_frame = ARDUINO_FRAME.replace("MOISTURE_PCT=62.1", "MOISTURE_PCT=120")

        self.assertEqual(parse_hc05_text(invalid_frame), {})

    def test_treats_alternate_sensor_sentinels_as_unmeasured(self):
        alternate_frame = ARDUINO_FRAME.replace("TEMP_C=26.3", "TEMP_C=-1")
        alternate_frame = alternate_frame.replace("MOISTURE_PCT=62.1", "MOISTURE_PCT=-1")
        alternate_frame = alternate_frame.replace("PH=5.8", "PH=-1")

        parsed = parse_hc05_text(alternate_frame)

        self.assertIsNone(parsed["temperature"])
        self.assertIsNone(parsed["soil_moisture"])
        self.assertIsNone(parsed["ph"])
