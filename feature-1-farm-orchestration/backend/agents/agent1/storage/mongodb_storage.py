# storage/mongodb_storage.py

from typing import List, Dict, Any, Optional

try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None


class MongoDBStorage:
    """
    MongoDB interface for storing pipeline data.

    This is optional and used for scalable storage.
    """

    def __init__(
        self,
        uri: str,
        database_name: str = "smart_farming",
        collection_name: str = "ingestion_data",
    ):
        if MongoClient is None:
            raise ImportError(
                "pymongo is not installed. Install it to use MongoDBStorage."
            )

        self.client = MongoClient(uri)
        self.db = self.client[database_name]
        self.collection = self.db[collection_name]

    # -----------------------------------
    # INSERT DATA
    # -----------------------------------
    def insert_data(self, data: List[Dict[str, Any]]) -> None:
        """
        Inserts multiple records into MongoDB.
        """
        if not data:
            return

        try:
            self.collection.insert_many(data)
            print(f"[INFO] Inserted {len(data)} records into MongoDB")

        except Exception as e:
            print(f"[ERROR] MongoDB insert failed: {e}")

    # -----------------------------------
    # FETCH DATA
    # -----------------------------------
    def fetch_data(
        self,
        query: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Fetch records from MongoDB.

        Args:
            query (dict): MongoDB query filter
            limit (int): Max number of records

        Returns:
            list: Retrieved records
        """

        try:
            cursor = self.collection.find(query or {}).limit(limit)
            return list(cursor)

        except Exception as e:
            print(f"[ERROR] MongoDB fetch failed: {e}")
            return []

    # -----------------------------------
    # DELETE DATA
    # -----------------------------------
    def delete_data(self, query: Dict[str, Any]) -> None:
        """
        Deletes records based on query.
        """

        try:
            result = self.collection.delete_many(query)
            print(f"[INFO] Deleted {result.deleted_count} records")

        except Exception as e:
            print(f"[ERROR] MongoDB delete failed: {e}")

    # -----------------------------------
    # CLOSE CONNECTION
    # -----------------------------------
    def close(self) -> None:
        """
        Closes MongoDB connection.
        """
        try:
            self.client.close()
            print("[INFO] MongoDB connection closed")
        except Exception as e:
            print(f"[ERROR] Failed to close MongoDB connection: {e}")
