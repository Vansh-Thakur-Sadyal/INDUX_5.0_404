# processors/cleaning.py

from typing import List, Dict, Any

class DataCleaner:
    def __init__(self):
        pass  # no internal state needed for now

    def clean_data(self, data: List[Dict[str, Any]], source: str = "unknown") -> List[Dict[str, Any]]:
        """
        Cleans and validates incoming data based on source type.

        Responsibilities:
        - Handle missing values
        - Remove invalid/outlier entries
        - Apply simple normalization
        - Ensure consistency for downstream processing

        Args:
            data (list): List of raw data records
            source (str): Source type (sensor, weather, image, market)

        Returns:
            list: Cleaned data records
        """

        cleaned_data = []

        for entry in data:
            if not isinstance(entry, dict):
                continue  # skip invalid format

            e = entry.copy()

            # -----------------------------------
            # COMMON VALIDATION
            # -----------------------------------
            if "field_id" not in e:
                continue

            # -----------------------------------
            # SENSOR DATA CLEANING
            # -----------------------------------
            if source == "sensor":
                soil_temp = e.get("soil_temperature")
                soil_moisture = e.get("soil_moisture")

                # Remove unrealistic temperature values
                if soil_temp is not None and (soil_temp < 0 or soil_temp > 60):
                    continue

                # Fill missing moisture
                if soil_moisture is None:
                    e["soil_moisture"] = 20.0  # default fallback

                # Normalize nutrients structure
                nutrients = e.get("soil_nutrients", {})
                e["soil_nutrients"] = {
                    "N": nutrients.get("N", 50),
                    "P": nutrients.get("P", 30),
                    "K": nutrients.get("K", 40),
                }

            # -----------------------------------
            # WEATHER DATA CLEANING
            # -----------------------------------
            elif source == "weather":
                temp = e.get("temperature")

                # Remove unrealistic temperature
                if temp is not None and (temp < -10 or temp > 60):
                    continue

                # Fill missing values
                e["humidity"] = e.get("humidity", 50.0)
                e["rain_forecast"] = e.get("rain_forecast", 0.0)

            # -----------------------------------
            # IMAGE DATA CLEANING
            # -----------------------------------
            elif source == "image":
                ndvi = e.get("ndvi_index")

                # Normalize NDVI (0 to 1)
                if ndvi is None or not (0 <= ndvi <= 1):
                    e["ndvi_index"] = 0.5  # neutral vegetation index

                # Ensure image source exists
                e["image_source"] = e.get("image_source", "unknown")

            # -----------------------------------
            # MARKET DATA CLEANING
            # -----------------------------------
            elif source == "market":
                price = e.get("market_price")

                # Remove invalid prices
                if price is None or price <= 0:
                    continue

                # Normalize trend
                if e.get("market_trend") not in ["up", "down", "stable"]:
                    e["market_trend"] = "stable"

            # -----------------------------------
            # FINAL STEP
            # -----------------------------------
            cleaned_data.append(e)

        return cleaned_data

# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    sample_data = [
        {"field_id": "F001", "soil_temperature": 25, "soil_moisture": 35, "soil_nutrients": {"N": 0.3}}
    ]
    cleaner = DataCleaner()
    print(cleaner.clean_data(sample_data, source="sensor"))
