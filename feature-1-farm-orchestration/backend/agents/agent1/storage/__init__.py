# storage/__init__.py
"""
Storage Package

Handles data persistence and buffering for the ingestion pipeline.

Modules:
- json_storage      : Save and load pipeline data to/from JSON (and optionally CSV)
- mongodb_storage   : Optional MongoDB integration for scalable storage
- buffer            : Local buffering for low-connectivity or intermittent environments

This layer ensures that processed data is safely stored and
accessible for downstream Agentic AI systems.
"""

from .json_storage import JSONStorage
from .buffer import Buffer

# Optional import (MongoDB may not always be used)
try:
    from .mongodb_storage import MongoDBStorage
except ImportError:
    MongoDBStorage = None

__all__ = [
    "save_to_json",
    "load_from_json",
    "DataBuffer",
    "MongoDBStorage",
]
