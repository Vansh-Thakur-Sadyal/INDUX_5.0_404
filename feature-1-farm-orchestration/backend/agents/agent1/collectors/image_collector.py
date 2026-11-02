import json
import random
from datetime import datetime
from pathlib import Path

# Load field metadata
METADATA_PATH = Path(__file__).parent.parent / "config" / "field_metadata.json"
with open(METADATA_PATH, "r") as f:
    FIELD_METADATA = json.load(f)

# -------------------------------
# NDVI Simulation Logic
# -------------------------------
def _simulate_ndvi(crop):
    """
    Simulate NDVI values based on crop type.
    NDVI range: 0 to 1
    Higher = healthier vegetation
    """
    crop_ndvi_ranges = {
        "Rice": (0.6, 0.9),
        "Wheat": (0.5, 0.85),
        "Cotton": (0.4, 0.8),
        "Sugarcane": (0.7, 0.95)
    }

    low, high = crop_ndvi_ranges.get(crop, (0.4, 0.8))
    return round(random.uniform(low, high), 2)

# -------------------------------
# ImageCollector class
# -------------------------------
class ImageCollector:
    def __init__(self):
        self.field_metadata = FIELD_METADATA

    def collect(self):
        """
        Returns simulated satellite/drone imagery insights (NDVI) for all fields.
        """
        all_data = []
        timestamp = datetime.utcnow().isoformat()

        for crop, fields in self.field_metadata.items():
            for field in fields:
                ndvi_value = _simulate_ndvi(crop)

                data_point = {
                    "crop": crop,
                    "field_id": field["field_id"],
                    "timestamp": timestamp,
                    "ndvi_index": ndvi_value,
                    "image_source": "simulated_satellite"
                }

                all_data.append(data_point)

        return all_data

# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    collector = ImageCollector()
    sample_data = collector.collect()
    print(json.dumps(sample_data[:3], indent=2))
