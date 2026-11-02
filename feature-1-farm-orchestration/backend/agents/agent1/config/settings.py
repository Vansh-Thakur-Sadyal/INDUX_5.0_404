# config/settings.py

import os

# -------------------------------
# Sampling / Ingestion Intervals (seconds)
# -------------------------------
SENSOR_SAMPLING_INTERVAL = 60          # Collect sensor data every 1 minute
WEATHER_FETCH_INTERVAL = 3600          # Fetch weather data every 1 hour
IMAGE_FETCH_INTERVAL = 86400           # Fetch satellite/drone imagery every 24 hours
MARKET_FETCH_INTERVAL = 7200           # Fetch market price data every 2 hours

# -------------------------------
# Storage Configuration
# -------------------------------
STORAGE_TYPE = os.getenv("STORAGE_TYPE", "json")  # Options: json, mongodb, influxdb
JSON_STORAGE_PATH = os.getenv("JSON_STORAGE_PATH", "storage/data.json")

# -------------------------------
# Weather API Configuration (if using real API)
# -------------------------------
WEATHER_API_KEY = os.getenv("WEATHER_API_KEY", "your_api_key_here")
WEATHER_API_URL = os.getenv(
    "WEATHER_API_URL",
    "https://api.openweathermap.org/data/2.5/weather"
)

# -------------------------------
# MQTT Sensor Configuration (for simulated sensors)
# -------------------------------
MQTT_BROKER = os.getenv("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "farm/sensors")

# -------------------------------
# Data Synchronization
# -------------------------------
TIME_WINDOW_HOURS = int(os.getenv("TIME_WINDOW_HOURS", 1))  # Align all data to this window

# -------------------------------
# Logging & Debug
# -------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")  # Options: DEBUG, INFO, WARNING, ERROR
