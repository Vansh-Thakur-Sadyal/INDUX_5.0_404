# collectors/__init__.py
"""
Collectors Package

This package contains all modular data source connectors for the Data Ingestion Agent:
- sensor_collector.py : Simulated/real sensor data ingestion
- weather_collector.py : Weather API polling / simulation
- image_collector.py : Satellite / drone imagery ingestion
- market_collector.py : Market pricing data ingestion

All collectors return data in a consistent format to be processed downstream.
"""
