import json
import random
from datetime import datetime
from pathlib import Path

# Load field metadata
METADATA_PATH = Path(__file__).parent.parent / "config" / "field_metadata.json"
with open(METADATA_PATH, "r") as f:
    FIELD_METADATA = json.load(f)

# -------------------------------
# Simulated Market Price Logic
# -------------------------------
def _simulate_market_price(crop):
    """
    Simulate market prices (₹ per quintal) based on crop type.
    """
    crop_price_ranges = {
        "Rice": (1800, 2500),
        "Wheat": (2000, 2800),
        "Cotton": (5000, 8000),
        "Sugarcane": (300, 400)
    }

    low, high = crop_price_ranges.get(crop, (1000, 3000))
    return round(random.uniform(low, high), 2)

# -------------------------------
# MarketCollector class
# -------------------------------
class MarketCollector:
    def __init__(self):
        self.field_metadata = FIELD_METADATA

    def collect(self):
        """
        Returns simulated market price data for all fields.
        """
        all_data = []
        timestamp = datetime.utcnow().isoformat()

        for crop, fields in self.field_metadata.items():
            for field in fields:
                price = _simulate_market_price(crop)

                data_point = {
                    "crop": crop,
                    "field_id": field["field_id"],
                    "region": field["region"],
                    "timestamp": timestamp,
                    "market_price": price,        # ₹ per quintal
                    "market_trend": random.choice(["up", "down", "stable"])
                }

                all_data.append(data_point)

        return all_data

# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    collector = MarketCollector()
    sample_data = collector.collect()
    print(json.dumps(sample_data[:3], indent=2))
