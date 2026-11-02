# tests/test_collectors.py

import pytest

from collectors.sensor_collector import SensorCollector
from collectors.weather_collector import WeatherCollector
from collectors.image_collector import ImageCollector
from collectors.market_collector import MarketCollector


# -----------------------------------
# FIXTURES
# -----------------------------------

@pytest.fixture
def sensor():
    return SensorCollector()


@pytest.fixture
def weather():
    return WeatherCollector()


@pytest.fixture
def image():
    return ImageCollector()


@pytest.fixture
def market():
    return MarketCollector()


# -----------------------------------
# SENSOR COLLECTOR TESTS
# -----------------------------------

def test_sensor_output(sensor):
    data = sensor.collect()

    assert isinstance(data, dict)
    assert "soil_moisture" in data
    assert "temperature" in data
    assert "humidity" in data


def test_sensor_value_ranges(sensor):
    data = sensor.collect()

    assert 0 <= data["soil_moisture"] <= 100
    assert -10 <= data["temperature"] <= 60
    assert 0 <= data["humidity"] <= 100


# -----------------------------------
# WEATHER COLLECTOR TESTS
# -----------------------------------

def test_weather_output(weather):
    data = weather.collect()

    assert isinstance(data, dict)
    assert "temperature" in data
    assert "humidity" in data


# -----------------------------------
# IMAGE COLLECTOR TESTS
# -----------------------------------

def test_image_output(image):
    data = image.collect()

    assert isinstance(data, dict)

    # flexible since images may vary
    assert "image_path" in data or "image_data" in data


# -----------------------------------
# MARKET COLLECTOR TESTS
# -----------------------------------

def test_market_output(market):
    data = market.collect()

    assert isinstance(data, dict)
    assert "crop_type" in data
    assert "price" in data


def test_market_price_positive(market):
    data = market.collect()

    assert data["price"] >= 0


# -----------------------------------
# CONSISTENCY TEST
# -----------------------------------

def test_collectors_return_dicts(sensor, weather, image, market):
    assert isinstance(sensor.collect(), dict)
    assert isinstance(weather.collect(), dict)
    assert isinstance(image.collect(), dict)
    assert isinstance(market.collect(), dict)
