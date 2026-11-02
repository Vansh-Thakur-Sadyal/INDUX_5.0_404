"""Health, capability and language endpoints."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter

from ..agents import registry
from ..core import languages, storage
from ..core.config import get_settings
from ..rag.store import store
from ..speech import azure_speech
from ..telephony import n8n_client

router = APIRouter(prefix="/api", tags=["system"])

VERSION = "2.0.0"


@router.get("/health")
async def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "Saarthi 2.0",
        "version": VERSION,
        "time": datetime.now(timezone.utc).isoformat(),
        "agents": len(registry.AGENTS),
        "knowledge_chunks": len(store.chunks),
        "storage": storage.counts(),
    }


@router.get("/keys/status")
async def keys_status() -> Dict[str, Any]:
    """Exactly what is live and what is not. The dashboard renders this verbatim."""
    settings = get_settings(reload=True)
    report = settings.capability_report()
    return {
        "capabilities": report,
        "configured_count": sum(1 for item in report if item["configured"]),
        "total": len(report),
        "config_file": "config/api_keys.env",
        "speech": azure_speech.status(),
        "telephony": n8n_client.status(),
        "rag": store.stats(),
    }


@router.get("/languages")
async def language_list() -> Dict[str, Any]:
    return {"languages": languages.as_dicts(), "default": languages.DEFAULT.code}


@router.get("/maps")
async def maps() -> Dict[str, Any]:
    """The browser Maps key, and nothing else about the configuration.

    A Maps JavaScript key is a public credential by design - it has to reach the
    browser to be used at all - and it is protected by the HTTP-referrer
    allowlist on the key itself, not by being kept secret. This endpoint exists
    so the key lives in `config/api_keys.env` with every other key rather than
    being written into a script that is served to everybody.
    """
    settings = get_settings()
    key = settings.google_maps_key
    return {
        "provider": "google-maps" if key else "openstreetmap",
        "key": key,
        "note": "" if key else
                ("GOOGLE_MAPS_API_KEY is not set, so the maps fall back to "
                 "OpenStreetMap tiles."),
    }
