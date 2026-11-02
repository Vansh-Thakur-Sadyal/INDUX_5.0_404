# storage/buffer.py

from typing import List, Dict, Any


class Buffer:
    """
    In-memory buffer for handling intermittent connectivity.

    Features:
    - Temporarily stores data when storage fails
    - Allows retry mechanism
    - Prevents data loss in unstable environments
    """

    def __init__(self, max_size: int = 1000):
        self.buffer: List[Dict[str, Any]] = []
        self.max_size = max_size

    # -----------------------------------
    # ADD DATA TO BUFFER
    # -----------------------------------
    def add(self, data: Dict[str, Any] or List[Dict[str, Any]]) -> None:
        """
        Adds new record(s) to buffer.
        Accepts single dict or list of dicts.
        """
        if not data:
            return

        # normalize single dict to list
        if isinstance(data, dict):
            data = [data]

        self.buffer.extend(data)

        # Prevent overflow
        if len(self.buffer) > self.max_size:
            overflow = len(self.buffer) - self.max_size
            self.buffer = self.buffer[overflow:]

        print(f"[INFO] Buffered {len(data)} records (Total: {len(self.buffer)})")

    # -----------------------------------
    # GET BUFFER DATA
    # -----------------------------------
    def get_all(self) -> List[Dict[str, Any]]:
        """
        Returns all buffered data.
        """
        return self.buffer.copy()

    # -----------------------------------
    # CLEAR BUFFER
    # -----------------------------------
    def clear(self) -> None:
        """
        Clears buffer after successful storage.
        """
        self.buffer = []
        print("[INFO] Buffer cleared")

    # -----------------------------------
    # FLUSH BUFFER TO STORAGE
    # -----------------------------------
    def flush(self, storage_object=None) -> None:
        """
        Attempts to write buffered data using a provided storage object.

        Args:
            storage_object (optional): Must have 'save' method like JSONStorage
        """
        if not self.buffer:
            print("[INFO] Buffer is empty, nothing to flush")
            return

        if storage_object is None:
            print("[WARNING] No storage object provided. Cannot flush buffer.")
            return

        try:
            storage_object.save(self.buffer)
            self.clear()
        except Exception as e:
            print(f"[WARNING] Flush failed, retaining buffer: {e}")


# -------------------------------
# Example usage
# -------------------------------
if __name__ == "__main__":
    from storage.json_storage import JSONStorage

    buf = Buffer(max_size=5)
    buf.add({"field_id": "F001", "value": 10})
    buf.add([{"field_id": "F002", "value": 20}])
    print(buf.get_all())

    # Example flush using JSONStorage
    storage = JSONStorage("output/buffer_test.json")
    buf.flush(storage)
