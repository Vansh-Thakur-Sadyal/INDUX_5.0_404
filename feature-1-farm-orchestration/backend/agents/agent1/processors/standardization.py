# processors/standardization.py

from typing import List, Dict, Any

class DataStandardizer:
    """
    Converts multi-source cleaned data into a unified, agent-compatible schema.
    """

    def __init__(self):
        pass  # No internal state needed

    def standardize_data(
        self,
        timestamp: str,
        sensor_data: List[Dict[str, Any]],
        weather_data: List[Dict[str, Any]],
        image_data: List[Dict[str, Any]],
        market_data: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Converts multi-source cleaned data into standardized records for pipeline consumption.

        Args:
            timestamp (str): Current pipeline timestamp
            sensor_data (list)
            weather_data (list)
            image_data (list)
            market_data (list)

        Returns:
            list: Standardized records
        """

        standardized_output = []

        # -----------------------------------------
        # CREATE FAST LOOKUP MAPS
        # -----------------------------------------
        weather_map = {w["field_id"]: w for w in weather_data}
        image_map = {i["field_id"]: i for i in image_data}
        market_map = {m["field_id"]: m for m in market_data}

        # -----------------------------------------
        # BUILD UNIFIED RECORD PER FIELD
        # -----------------------------------------
        for sensor in sensor_data:
            field_id = sensor["field_id"]

            weather = weather_map.get(field_id, {})
            image = image_map.get(field_id, {})
            market = market_map.get(field_id, {})

            record = {
                "timestamp": timestamp,
                "field_id": field_id,
                "crop": sensor.get("crop"),

                "sensor_data": {
                    "soil_moisture": sensor.get("soil_moisture"),
                    "soil_temperature": sensor.get("soil_temperature"),
                    "soil_nutrients": sensor.get("soil_nutrients"),
                },

                "weather_data": {
                    "temperature": weather.get("temperature"),
                    "humidity": weather.get("humidity"),
                    "rain_forecast": weather.get("rain_forecast"),
                },

                "image_data": {
                    "ndvi_index": image.get("ndvi_index"),
                    "image_source": image.get("image_source"),
                },

                "market_data": {
                    "market_price": market.get("market_price"),
                    "market_trend": market.get("market_trend"),
                },

                "meta": {
                    "data_sources": {
                        "sensor": bool(sensor),
                        "weather": bool(weather),
                        "image": bool(image),
                        "market": bool(market),
                    },
                    "version": "1.0",
                },
            }

            standardized_output.append(record)

        return standardized_output


# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    from datetime import datetime

    timestamp = datetime.utcnow().isoformat()
    sample_sensor = [{"field_id": "F001", "crop": "Rice", "soil_moisture": 30, "soil_temperature": 25, "soil_nutrients": {"N": 0.3, "P": 0.2, "K": 0.1}}]
    sample_weather = [{"field_id": "F001", "temperature": 28, "humidity": 60, "rain_forecast": 5}]
    sample_image = [{"field_id": "F001", "ndvi_index": 0.8, "image_source": "satellite"}]
    sample_market = [{"field_id": "F001", "market_price": 2200, "market_trend": "up"}]

    standardizer = DataStandardizer()
    standardized_data = standardizer.standardize_data(timestamp, sample_sensor, sample_weather, sample_image, sample_market)
    print(standardized_data)
