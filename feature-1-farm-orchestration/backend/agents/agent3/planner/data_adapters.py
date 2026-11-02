"""
Real-Time Data Adapters
=======================
Connects to external APIs and teammate modules.
Replace mock data with real API calls where marked.

APIs Used:
  - OpenWeatherMap (weather windows)         → Free tier available
  - IMD Open Data (Indian Met Dept)          → Free
  - Agmarknet (mandi market prices)          → Free
  - NASA POWER (solar/weather agri data)     → Free
  - SoilGrids (soil data)                    → Free
  - Sentinel Hub (satellite imagery)         → Freemium
"""

import os
import requests
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from agent import WeatherWindow, RiskSignal

logger = logging.getLogger("DataAdapters")


# ─────────────────────────────────────────────
# 1. Weather Adapter (OpenWeatherMap)
#    API KEY: Get free at https://openweathermap.org/api
# ─────────────────────────────────────────────

class WeatherAdapter:
    BASE_URL = "https://api.openweathermap.org/data/2.5"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("OPENWEATHER_API_KEY", "YOUR_KEY_HERE")

    def get_weather_windows(self, field_id: str, lat: float, lon: float) -> List[WeatherWindow]:
        """
        Fetch 5-day hourly forecast and extract safe operation windows.
        """
        try:
            url = f"{self.BASE_URL}/forecast"
            params = {
                "lat": lat,
                "lon": lon,
                "appid": self.api_key,
                "units": "metric",
                "cnt": 16,  # next 48 hours in 3h steps
            }
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            windows = []
            for item in data.get("list", []):
                dt = datetime.utcfromtimestamp(item["dt"])
                wind_speed = item["wind"]["speed"] * 3.6  # m/s to km/h
                rain_prob = item.get("pop", 0.0)
                temp = item["main"]["temp"]

                windows.append(WeatherWindow(
                    field_id=field_id,
                    start_time=dt,
                    end_time=dt + timedelta(hours=3),
                    wind_speed_kmh=wind_speed,
                    rain_probability=rain_prob,
                    temperature_c=temp,
                    is_spray_safe=(wind_speed < 15 and rain_prob < 0.3 and 10 < temp < 35),
                    is_irrigation_needed=(rain_prob < 0.2),
                ))
            return windows

        except Exception as e:
            logger.warning(f"WeatherAdapter error: {e}. Using mock data.")
            return self._mock_windows(field_id)

    def _mock_windows(self, field_id: str) -> List[WeatherWindow]:
        now = datetime.utcnow()
        return [
            WeatherWindow(
                field_id=field_id,
                start_time=now + timedelta(hours=1),
                end_time=now + timedelta(hours=5),
                wind_speed_kmh=8.0,
                rain_probability=0.1,
                temperature_c=28.0,
                is_spray_safe=True,
                is_irrigation_needed=True,
            )
        ]


# ─────────────────────────────────────────────
# 2. NASA POWER Adapter (solar radiation, humidity, ET0)
#    Free, no key needed
#    Docs: https://power.larc.nasa.gov/
# ─────────────────────────────────────────────

class NASAPowerAdapter:
    BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

    def get_evapotranspiration(self, lat: float, lon: float) -> Optional[float]:
        """Returns reference ET0 (mm/day) for irrigation need estimation."""
        today = datetime.utcnow()
        params = {
            "parameters": "EVPTRNS",
            "community": "AG",
            "longitude": lon,
            "latitude": lat,
            "start": (today - timedelta(days=3)).strftime("%Y%m%d"),
            "end": today.strftime("%Y%m%d"),
            "format": "JSON",
        }
        try:
            resp = requests.get(self.BASE_URL, params=params, timeout=15)
            data = resp.json()
            values = list(data["properties"]["parameter"]["EVPTRNS"].values())
            return sum(values) / len(values) if values else None
        except Exception as e:
            logger.warning(f"NASA POWER error: {e}")
            return 5.5  # fallback mm/day


# ─────────────────────────────────────────────
# 3. Agmarknet Market Price Adapter
#    Free, no key: https://agmarknet.gov.in/
#    Note: Use unofficial scraper or data.gov.in API
#    data.gov.in API key: Register at https://data.gov.in/
# ─────────────────────────────────────────────

class MarketPriceAdapter:
    BASE_URL = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("DATA_GOV_IN_API_KEY", "YOUR_KEY_HERE")

    def get_mandi_price(self, commodity: str, state: str = "Rajasthan") -> Optional[float]:
        """Returns modal price (INR/quintal) for a commodity."""
        try:
            params = {
                "api-key": self.api_key,
                "format": "json",
                "filters[commodity]": commodity,
                "filters[state]": state,
                "limit": 5,
            }
            resp = requests.get(self.BASE_URL, params=params, timeout=10)
            data = resp.json()
            records = data.get("records", [])
            if records:
                return float(records[0].get("modal_price", 0))
        except Exception as e:
            logger.warning(f"Market price error: {e}")
        return None


# ─────────────────────────────────────────────
# 4. Soil Risk Adapter (SoilGrids REST)
#    Free, no key: https://rest.isric.org/soilgrids/v2.0/
# ─────────────────────────────────────────────

class SoilRiskAdapter:
    BASE_URL = "https://rest.isric.org/soilgrids/v2.0/properties/query"

    def get_nutrient_risk(self, lat: float, lon: float, field_id: str) -> Optional[RiskSignal]:
        """Returns nutrient_gap risk signal based on soil nitrogen."""
        try:
            params = {
                "lon": lon,
                "lat": lat,
                "property": "nitrogen",
                "depth": "0-5cm",
                "value": "mean",
            }
            resp = requests.get(self.BASE_URL, params=params, timeout=15)
            data = resp.json()
            layers = data.get("properties", {}).get("layers", [])
            if layers:
                n_value = layers[0]["depths"][0]["values"]["mean"]
                # nitrogen in cg/kg; threshold ~150 cg/kg
                if n_value and n_value < 150:
                    severity = max(0.0, min(1.0, (150 - n_value) / 150))
                    return RiskSignal(
                        risk_type="nutrient_gap",
                        severity=severity,
                        affected_field_id=field_id,
                        confidence=0.75,
                        metadata={"nitrogen_cg_kg": n_value},
                    )
        except Exception as e:
            logger.warning(f"SoilGrids error: {e}")
        return None


# ─────────────────────────────────────────────
# 5. Teammate Module Interface
#    These stubs let you connect teammate outputs
# ─────────────────────────────────────────────

class TeammateRiskAdapter:
    """
    Plug in outputs from:
      - Sensor Agent (soil moisture, NPK sensors)
      - Imagery Agent (drone/satellite NDVI)
      - Disease Detection Agent (image classification)

    Expected schema: List[RiskSignal]
    """

    @staticmethod
    def from_sensor_agent(sensor_output: dict) -> List[RiskSignal]:
        """
        Convert sensor agent output to RiskSignal list.
        sensor_output = {
            "field_id": "F001",
            "soil_moisture_percent": 18.5,
            "ndvi": 0.42,
            "temperature_c": 34.2
        }
        """
        risks = []
        field_id = sensor_output.get("field_id", "UNKNOWN")

        moisture = sensor_output.get("soil_moisture_percent")
        if moisture is not None and moisture < 25:
            severity = max(0.0, min(1.0, (25 - moisture) / 25))
            risks.append(RiskSignal(
                risk_type="water_stress",
                severity=severity,
                affected_field_id=field_id,
                confidence=0.90,
                metadata={"soil_moisture_percent": moisture},
            ))

        ndvi = sensor_output.get("ndvi")
        if ndvi is not None and ndvi < 0.4:
            severity = max(0.0, min(1.0, (0.4 - ndvi) / 0.4))
            risks.append(RiskSignal(
                risk_type="nutrient_gap",
                severity=severity,
                affected_field_id=field_id,
                confidence=0.70,
                metadata={"ndvi": ndvi},
            ))

        return risks

    @staticmethod
    def from_imagery_agent(imagery_output: dict) -> List[RiskSignal]:
        """
        imagery_output = {
            "field_id": "F001",
            "pest_detection_score": 0.72,
            "disease_detection_score": 0.45,
        }
        """
        risks = []
        field_id = imagery_output.get("field_id", "UNKNOWN")

        pest = imagery_output.get("pest_detection_score", 0)
        if pest > 0.5:
            risks.append(RiskSignal(
                risk_type="pest",
                severity=pest,
                affected_field_id=field_id,
                confidence=0.80,
            ))

        disease = imagery_output.get("disease_detection_score", 0)
        if disease > 0.5:
            risks.append(RiskSignal(
                risk_type="disease",
                severity=disease,
                affected_field_id=field_id,
                confidence=0.75,
            ))

        return risks
