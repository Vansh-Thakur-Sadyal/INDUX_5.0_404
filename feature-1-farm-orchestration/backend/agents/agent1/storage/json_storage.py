# storage/json_storage.py

import json
import os
from typing import List, Dict, Any

class JSONStorage:
    def __init__(self, file_path: str):
        self.file_path = file_path  # store default file path

    # -----------------------------------
    # SAVE
    # -----------------------------------
    def save(self, data: List[Dict[str, Any]]) -> None:
        """
        Saves data to the default file path.
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.file_path), exist_ok=True)

            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)

            print(f"[INFO] Data successfully saved to {self.file_path}")

        except Exception as e:
            print(f"[ERROR] Failed to save JSON: {e}")

    # -----------------------------------
    # LOAD
    # -----------------------------------
    def load(self) -> List[Dict[str, Any]]:
        """
        Loads data from the default file path.
        """
        if not os.path.exists(self.file_path):
            print(f"[WARNING] File not found: {self.file_path}")
            return []

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            return data

        except Exception as e:
            print(f"[ERROR] Failed to load JSON: {e}")
            return []

    # -----------------------------------
    # APPEND
    # -----------------------------------
    def append(self, data: List[Dict[str, Any]]) -> None:
        """
        Appends new records to the default JSON file.
        """
        existing_data = self.load()

        if not isinstance(existing_data, list):
            existing_data = []

        combined_data = existing_data + data
        self.save(combined_data)


# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    storage = JSONStorage("output/sample.json")
    sample_data = [{"field_id": "F001", "value": 42}]

    # Save new data
    storage.save(sample_data)

    # Load data
    loaded = storage.load()
    print(loaded)

    # Append new records
    storage.append([{"field_id": "F002", "value": 55}])
    print(storage.load())
