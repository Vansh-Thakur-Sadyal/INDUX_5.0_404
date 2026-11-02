# tests/test_storage.py

import os
import pytest

from storage.json_storage import JSONStorage
from storage.mongodb_storage import MongoDBStorage
from storage.buffer import Buffer


# -----------------------------------
# SAMPLE DATA
# -----------------------------------

@pytest.fixture
def sample_data():
    return {
        "field_id": "F001",
        "soil_moisture": 45,
        "temperature": 30,
        "humidity": 70,
        "crop_type": "rice",
        "timestamp": "2026-04-08T10:00:00"
    }


# -----------------------------------
# JSON STORAGE TESTS
# -----------------------------------

@pytest.fixture
def json_storage(tmp_path):
    """
    Use pytest temp directory to avoid real file writes.
    """
    file_path = tmp_path / "test_data.json"
    return JSONStorage(file_path=str(file_path))


def test_json_save_and_load(json_storage, sample_data):
    json_storage.save(sample_data)
    loaded = json_storage.load()

    assert loaded is not None
    assert isinstance(loaded, list) or isinstance(loaded, dict)


def test_json_file_created(json_storage, sample_data):
    json_storage.save(sample_data)

    assert os.path.exists(json_storage.file_path)


# -----------------------------------
# BUFFER TESTS
# -----------------------------------

@pytest.fixture
def buffer():
    return Buffer(max_size=5)


def test_buffer_add(buffer, sample_data):
    buffer.add(sample_data)

    assert len(buffer.buffer) == 1


def test_buffer_overflow(buffer, sample_data):
    for _ in range(10):
        buffer.add(sample_data)

    assert len(buffer.buffer) <= buffer.max_size


def test_buffer_flush(buffer, sample_data):
    buffer.add(sample_data)
    flushed = buffer.flush()

    assert isinstance(flushed, list)
    assert len(flushed) > 0
    assert len(buffer.buffer) == 0


# -----------------------------------
# MONGODB TESTS (SAFE / OPTIONAL)
# -----------------------------------

@pytest.fixture
def mongodb():
    """
    Initialize MongoDB storage.
    Will not fail tests if DB is not running.
    """
    try:
        return MongoDBStorage(db_name="test_db", collection="test_collection")
    except Exception:
        return None


def test_mongodb_insert(mongodb, sample_data):
    if mongodb is None:
        pytest.skip("MongoDB not available")

    result = mongodb.insert(sample_data)

    assert result is not None


def test_mongodb_fetch(mongodb):
    if mongodb is None:
        pytest.skip("MongoDB not available")

    data = mongodb.fetch_all()

    assert isinstance(data, list)
