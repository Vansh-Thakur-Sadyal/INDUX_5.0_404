# ==========================================
# AGENT1 API ROUTES (FIXED + PRODUCTION READY)
# ==========================================

from fastapi import APIRouter, Query
from typing import List, Dict, Any

# ✅ FIXED IMPORT (CRITICAL)
from agent1.storage.json_storage import JSONStorage


class DataAPI:
    """
    Encapsulates all FastAPI routes for field data access.
    """

    def __init__(self, data_file_path: str = None):
        self.router = APIRouter()

        # ✅ SAFE DEFAULT PATH
        if data_file_path:
            self.DATA_FILE_PATH = data_file_path
        else:
            self.DATA_FILE_PATH = "backend/agents/agent1/data/processed_data.json"

        self._register_routes()

    def _register_routes(self):

        # -------------------------------
        # GET: All Latest Field Data
        # -------------------------------
        @self.router.get("/latest-field-data", response_model=List[Dict[str, Any]])
        def get_latest_field_data():
            storage = JSONStorage(self.DATA_FILE_PATH)
            data = storage.load()
            return data

        # -------------------------------
        # GET: Specific Field Data
        # -------------------------------
        @self.router.get("/field/{field_id}", response_model=Dict[str, Any])
        def get_field_data(field_id: str):
            storage = JSONStorage(self.DATA_FILE_PATH)
            data = storage.load()

            for record in reversed(data):
                if record.get("field_id") == field_id:
                    return record

            return {"message": "Field not found"}

        # -------------------------------
        # GET: Filter by Reliability
        # -------------------------------
        @self.router.get("/reliable-data", response_model=List[Dict[str, Any]])
        def get_reliable_data(threshold: float = Query(0.7, ge=0.0, le=1.0)):
            storage = JSONStorage(self.DATA_FILE_PATH)
            data = storage.load()

            return [
                record
                for record in data
                if record.get("reliability_score", 0) >= threshold
            ]

        # -------------------------------
        # GET: Filter by Crop Type
        # -------------------------------
        @self.router.get("/crop/{crop_name}", response_model=List[Dict[str, Any]])
        def get_data_by_crop(crop_name: str):
            storage = JSONStorage(self.DATA_FILE_PATH)
            data = storage.load()

            return [
                record
                for record in data
                if record.get("crop", "").lower() == crop_name.lower()
            ]

        # -------------------------------
        # GET: Health Check
        # -------------------------------
        @self.router.get("/health")
        def health_check():
            return {
                "status": "ok",
                "service": "agent1_data_api"
            }
