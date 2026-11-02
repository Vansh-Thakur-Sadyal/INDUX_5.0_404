# ==========================================
# WEATHER COLLECTOR (FINAL - FIXED IMPORTS)
# ==========================================

import json
import random
from datetime import datetime
from pathlib import Path

# ✅ FIXED IMPORT (CRITICAL)
from agent1.config.settings import WEATHER_API_KEY, WEATHER_API_URL

# ==========================================
# LOAD FIELD METADATA
# ==========================================

BASE_DIR = Path(__file__).resolve().parent.parent
METADATA_PATH = BASE_DIR / "config" / "field_metadata.json"

with open(METADATA_PATH, "r") as f:
    FIELD_METADATA = json.load(f)

# ==========================================
# SIMULATED WEATHER GENERATOR
# ==========================================

def _simulate_weather(region):
    """
    Generate realistic simulated weather data based on region.
    """
    return {
        "temperature": round(random.uniform(20, 40), 1),   # °C
        "humidity": round(random.uniform(40, 90), 1),      # %
        "rain_forecast": round(random.uniform(0, 20), 1),  # mm
        "wind_speed": round(random.uniform(0, 10), 1)      # m/s
    }

# ==========================================
# WEATHER COLLECTOR CLASS
# ==========================================

class WeatherCollector:
    def __init__(self):
        self.field_metadata = FIELD_METADATA

    def collect(self):
        """
        Returns simulated weather data for all fields.
        """
        all_data = []
        timestamp = datetime.utcnow().isoformat()

        for crop, fields in self.field_metadata.items():
            for field in fields:

                weather = _simulate_weather(field["region"])

                data_point = {
                    "crop": crop,
                    "field_id": field["field_id"],
                    "region": field["region"],
                    "timestamp": timestamp,
                    "temperature": weather["temperature"],
                    "humidity": weather["humidity"],
                    "rain_forecast": weather["rain_forecast"],
                    "wind_speed": weather["wind_speed"]
                }

                all_data.append(data_point)

        return all_data

# ==========================================
# OPTIONAL REAL API (FUTURE)
# ==========================================

def _fetch_weather_from_api(region):
    """
    Placeholder for real API integration.
    """
    # Example:
    # import requests
    # params = {"q": region, "appid": WEATHER_API_KEY, "units": "metric"}
    # response = requests.get(WEATHER_API_URL, params=params)
    # return response.json()
    pass

# ==========================================
# TEST MODE
# ==========================================

if __name__ == "__main__":
    collector = WeatherCollector()
    sample_data = collector.collect()

    print(json.dumps(sample_data[:3], indent=2))
