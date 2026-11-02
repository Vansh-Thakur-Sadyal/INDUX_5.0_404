# tests/test_processors.py

import pytest

from processors.cleaning import DataCleaner
from processors.standardization import DataStandardizer
from processors.synchronization import DataSynchronizer
from processors.reliability import ReliabilityScorer


# -----------------------------------
# SAMPLE RAW DATA (used across tests)
# -----------------------------------

@pytest.fixture
def raw_data():
    return {
        "field_id": "F001",
        "soil_moisture": 120,   # invalid (should be capped)
        "temperature": None,    # missing value
        "humidity": 85,
        "crop": "Rice",
        "timestamp": "2026-04-08T10:00:00"
    }


# -----------------------------------
# CLEANING TESTS
# -----------------------------------

@pytest.fixture
def cleaner():
    return DataCleaner()


def test_cleaning_output_structure(cleaner, raw_data):
    cleaned = cleaner.clean(raw_data)

    assert isinstance(cleaned, dict)


def test_cleaning_handles_missing_values(cleaner, raw_data):
    cleaned = cleaner.clean(raw_data)

    assert cleaned["temperature"] is not None, "Missing values not handled"


def test_cleaning_caps_values(cleaner, raw_data):
    cleaned = cleaner.clean(raw_data)

    assert 0 <= cleaned["soil_moisture"] <= 100, "Soil moisture not normalized"


# -----------------------------------
# STANDARDIZATION TESTS
# -----------------------------------

@pytest.fixture
def standardizer():
    return DataStandardizer()


def test_standardization_schema(standardizer, raw_data):
    standardized = standardizer.standardize(raw_data)

    required_keys = [
        "field_id",
        "timestamp",
        "soil_moisture",
        "temperature",
        "humidity",
        "crop_type"
    ]

    for key in required_keys:
        assert key in standardized, f"Missing standardized key: {key}"


def test_standardization_types(standardizer, raw_data):
    standardized = standardizer.standardize(raw_data)

    assert isinstance(standardized["field_id"], str)
    assert isinstance(standardized["soil_moisture"], (int, float))


# -----------------------------------
# SYNCHRONIZATION TESTS
# -----------------------------------

@pytest.fixture
def synchronizer():
    return DataSynchronizer()


def test_synchronization_output(synchronizer):
    data_list = [
        {"timestamp": "2026-04-08T10:00:00", "soil_moisture": 40},
        {"timestamp": "2026-04-08T10:05:00", "temperature": 30},
    ]

    synced = synchronizer.synchronize(data_list)

    assert isinstance(synced, list), "Synchronized output should be a list"


def test_synchronization_not_empty(synchronizer):
    data_list = [
        {"timestamp": "2026-04-08T10:00:00", "soil_moisture": 40}
    ]

    synced = synchronizer.synchronize(data_list)

    assert len(synced) > 0


# -----------------------------------
# RELIABILITY SCORE TESTS
# -----------------------------------

@pytest.fixture
def scorer():
    return ReliabilityScorer()


def test_reliability_score_range(scorer, raw_data):
    score = scorer.compute(raw_data)

    assert 0 <= score <= 1, "Reliability score must be between 0 and 1"


def test_reliability_output_type(scorer, raw_data):
    score = scorer.compute(raw_data)

    assert isinstance(score, float)


# -----------------------------------
# PIPELINE CONSISTENCY TEST
# -----------------------------------

def test_processing_pipeline(cleaner, standardizer, scorer, raw_data):
    cleaned = cleaner.clean(raw_data)
    standardized = standardizer.standardize(cleaned)
    score = scorer.compute(standardized)

    assert isinstance(standardized, dict)
    assert "field_id" in standardized
    assert 0 <= score <= 1
