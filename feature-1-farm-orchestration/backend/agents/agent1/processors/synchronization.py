# processors/synchronization.py

from typing import List, Dict, Any
from datetime import datetime

class DataSynchronizer:
    def __init__(self):
        pass  # No internal state needed

    def synchronize(self, data: List[Dict[str, Any]], window: str = "hourly") -> List[Dict[str, Any]]:
        """
        Synchronizes timestamps across all records into fixed time windows.

        Purpose:
        - Align multi-source data collected at different frequencies
        - Ensure consistency for downstream Agentic AI models

        Args:
            data (list): Data records (each record is a dict)
            window (str): Time window type ("hourly", "daily")

        Returns:
            list: Time-aligned data
        """
        synchronized_data = []

        for entry in data:
            e = entry.copy()
            ts = e.get("timestamp")

            if not ts:
                continue

            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue  # skip invalid timestamps

            # -----------------------------------
            # TIME WINDOW ALIGNMENT
            # -----------------------------------
            if window == "hourly":
                dt = dt.replace(minute=0, second=0, microsecond=0)
            elif window == "daily":
                dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)

            # Update timestamp
            e["timestamp"] = dt.isoformat()

            # -----------------------------------
            # ADD SYNCHRONIZATION META
            # -----------------------------------
            if "meta" not in e:
                e["meta"] = {}

            e["meta"]["time_window"] = window
            e["meta"]["synchronized"] = True

            synchronized_data.append(e)

        return synchronized_data

# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    sample_data = [
        {"field_id": "F001", "timestamp": "2026-04-08T14:23:45", "crop": "Rice"}
    ]
    synchronizer = DataSynchronizer()
    synced = synchronizer.synchronize(sample_data, window="hourly")
    print(synced)
