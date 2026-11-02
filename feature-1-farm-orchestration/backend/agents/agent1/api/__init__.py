# api/__init__.py
"""
API Package

This package exposes standardized pipeline data to downstream systems
and Agentic AI models via FastAPI routes.

Modules:
- routes : Defines all API endpoints for accessing processed data

Purpose:
- Acts as the interface layer of the ingestion pipeline
- Ensures consistent and structured data access
- Enables seamless integration with other agents and services
"""

from .routes import DataAPI

__all__ = ["DataAPI"]
