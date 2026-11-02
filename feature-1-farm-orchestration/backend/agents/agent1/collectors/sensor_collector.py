import json
import random
from datetime import datetime
from pathlib import Path

# Load field metadata
METADATA_PATH = Path(__file__).parent.parent / "config" / "field_metadata.json"
with open(METADATA_PATH, "r") as f:
    FIELD_METADATA = json.load(f)

# -------------------------------
# SensorCollector class
# -------------------------------
class SensorCollector:
    def __init__(self):
        """
        Initializes the collector. You can add configuration here if needed.
        """
        self.field_metadata = FIELD_METADATA

    def collect(self):
        """
        Returns simulated sensor readings for all fields.
        Each reading includes:
        - field_id
        - timestamp
        - soil_moisture (%)
        - soil_temperature (°C)
        - soil_nutrients (NPK ratio)
        """
        all_data = []
        timestamp = datetime.utcnow().isoformat()

        for crop, fields in self.field_metadata.items():
            for field in fields:
                for sensor_id in field["sensor_ids"]:
                    data_point = {
                        "crop": crop,
                        "field_id": field["field_id"],
                        "sensor_id": sensor_id,
                        "timestamp": timestamp,
                        "soil_moisture": round(random.uniform(10, 40), 1),      # %
                        "soil_temperature": round(random.uniform(20, 40), 1),   # °C
                        "soil_nutrients": {
                            "N": round(random.uniform(0.1, 0.5), 2),            # Nitrogen %
                            "P": round(random.uniform(0.05, 0.3), 2),           # Phosphorus %
                            "K": round(random.uniform(0.1, 0.4), 2)             # Potassium %
                        }
                    }
                    all_data.append(data_point)

        return all_data

# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    collector = SensorCollector()
    sample_data = collector.collect()
    print(json.dumps(sample_data[:3], indent=2))  # print first 3 entries for demo
