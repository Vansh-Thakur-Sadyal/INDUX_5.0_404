"""Configuration loader for Saarthi 2.0.

Single source of truth: config/api_keys.env at the project root.
Environment variables override file values (useful for Docker/CI).
Nothing in this module ever raises on a missing key - callers ask
`settings.has(...)` and degrade visibly instead.
"""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_FILE = PROJECT_ROOT / "config" / "api_keys.env"
DATA_DIR = PROJECT_ROOT / "data"
KNOWLEDGE_DIR = DATA_DIR / "knowledge"
STORAGE_DIR = DATA_DIR / "storage"
FRONTEND_DIR = PROJECT_ROOT / "frontend"
LANDING_DIR = PROJECT_ROOT / "landing"      # Saarthi's public site, served at /
AGENCY_DIR = PROJECT_ROOT / "agency"        # the VisionCoders site, its own host
CONSOLE_DIR = PROJECT_ROOT / "console"      # the knowledge console, its own host

_PLACEHOLDER_MARKERS = ("_here", "your_", "change-me", "xxxx", "<", "paste")


def _is_placeholder(value: str) -> bool:
    v = (value or "").strip()
    if not v:
        return True
    low = v.lower()
    return any(marker in low for marker in _PLACEHOLDER_MARKERS)


def _parse_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        values[key.strip()] = val
    return values


@dataclass
class Settings:
    """Resolved runtime configuration."""

    values: Dict[str, str] = field(default_factory=dict)

    # ---------- generic access ----------
    def get(self, key: str, default: str = "") -> str:
        env = os.environ.get(key)
        if env is not None and env.strip() != "":
            return env.strip()
        return (self.values.get(key) or default).strip()

    def has(self, key: str) -> bool:
        return not _is_placeholder(self.get(key))

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get(key) or default)
        except (TypeError, ValueError):
            return default

    # ---------- named settings ----------
    @property
    def mistral_api_key(self) -> str:
        return self.get("MISTRAL_API_KEY")

    @property
    def mistral_model(self) -> str:
        return self.get("MISTRAL_MODEL", "mistral-small-latest")

    @property
    def mistral_embed_model(self) -> str:
        return self.get("MISTRAL_EMBED_MODEL", "mistral-embed")

    @property
    def mistral_fallback_models(self) -> List[str]:
        """Tried in order when a model is rate-limited (429) or off-tier (403).

        A chain rather than a single name because this account also returns
        "Not enough capacity available" on individual models.
        """
        raw = self.get("MISTRAL_FALLBACK_MODEL", "ministral-8b-latest,ministral-3b-latest")
        return [name.strip() for name in raw.split(",") if name.strip()]

    @property
    def azure_speech_key(self) -> str:
        return self.get("AZURE_SPEECH_KEY")

    @property
    def azure_speech_region(self) -> str:
        return self.get("AZURE_SPEECH_REGION", "centralindia")

    @property
    def google_maps_key(self) -> str:
        return self.get("GOOGLE_MAPS_API_KEY")

    @property
    def n8n_base_url(self) -> str:
        return self.get("N8N_BASE_URL").rstrip("/")

    @property
    def n8n_webhook_token(self) -> str:
        return self.get("N8N_WEBHOOK_TOKEN")

    @property
    def inbound_token(self) -> str:
        return self.get("SAARTHI_INBOUND_TOKEN")

    @property
    def helpline_number(self) -> str:
        return self.get("SAARTHI_HELPLINE_NUMBER")

    @property
    def data_gov_api_key(self) -> str:
        return self.get("DATA_GOV_API_KEY")

    @property
    def data_gov_resource_id(self) -> str:
        return self.get("DATA_GOV_RESOURCE_ID", "9ef84268-d588-465a-a308-a864a43d0070")

    @property
    def mongodb_uri(self) -> str:
        return self.get("MONGODB_URI")

    @property
    def public_url(self) -> str:
        url = self.get("SAARTHI_PUBLIC_URL")
        if url:
            return url.rstrip("/")
        return f"http://{self.get('SAARTHI_HOST', '127.0.0.1')}:{self.get_int('SAARTHI_PORT', 8010)}"

    def n8n_url(self, path_key: str, default_path: str) -> str:
        path = self.get(path_key, default_path)
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not self.n8n_base_url:
            return ""
        return f"{self.n8n_base_url}/{path.lstrip('/')}"

    # ---------- capability report ----------
    def capability_report(self) -> List[dict]:
        """What is live and what is not - rendered verbatim on the dashboard."""
        return [
            {
                "id": "llm",
                "name": "Mistral LLM",
                "keys": ["MISTRAL_API_KEY"],
                "configured": self.has("MISTRAL_API_KEY"),
                "powers": "Answers with citations, retrieval, reranking, fact "
                          "extraction and translation - everything that reasons",
                "without_it": "Agents and RAG still run; no generated prose - the reply says so.",
            },
            {
                "id": "embeddings",
                "name": "Mistral embeddings (RAG)",
                "keys": ["MISTRAL_API_KEY"],
                "configured": self.has("MISTRAL_API_KEY"),
                "powers": "Semantic retrieval over the agricultural knowledge base",
                "without_it": "Falls back to a deterministic offline embedder, labelled as such.",
            },
            {
                "id": "sarvam",
                "name": "Sarvam AI (voice)",
                "keys": ["SARVAM_API_KEY"],
                "configured": self.has("SARVAM_API_KEY"),
                "powers": "The spoken half of a call - conversation, and the "
                          "Indian-language voice it is said in",
                "without_it": "Mistral carries the conversation instead: correct, "
                              "about 470 ms slower per turn, and weaker Hindi.",
            },
            {
                "id": "speech",
                "name": "Azure Speech",
                "keys": ["AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"],
                "configured": self.has("AZURE_SPEECH_KEY"),
                "powers": "Multilingual speech-to-text and text-to-speech",
                "without_it": "Microphone and spoken replies are disabled; text chat still works.",
            },
            {
                "id": "maps",
                "name": "Google Maps Platform",
                "keys": ["GOOGLE_MAPS_API_KEY"],
                "configured": self.has("GOOGLE_MAPS_API_KEY"),
                "powers": "Finding a village by name, and naming the place under a "
                          "pin so the profile fills itself in",
                "without_it": "OpenStreetMap answers instead - the same source as the "
                              "map tiles, thinner on small hamlets.",
            },
            {
                "id": "telephony",
                "name": "Telephony (Twilio via n8n)",
                "keys": ["N8N_BASE_URL", "N8N_OUTBOUND_CALL_PATH"],
                "configured": bool(self.n8n_url("N8N_OUTBOUND_CALL_PATH", "")),
                "powers": "Feature-phone calls, outbound call-backs, IVR",
                "without_it": "Calls are queued as clearly labelled simulations, never placed.",
            },
            {
                "id": "market",
                "name": "data.gov.in Agmarknet",
                "keys": ["DATA_GOV_API_KEY"],
                "configured": self.has("DATA_GOV_API_KEY"),
                "powers": "Live mandi prices for Agent 12",
                "without_it": "Market answers state that live prices are unavailable.",
            },
            {
                "id": "vision",
                "name": "Crop disease model",
                "keys": [],
                "configured": (DATA_DIR / "models" / "leaf_disease.onnx").exists(),
                "powers": "Screens an uploaded leaf photo against the trained classes",
                "without_it": ("Photo screening is off; the Pest page still reasons from "
                               "your description. Train and export it from ml/."),
            },
            {
                "id": "weather",
                "name": "Open-Meteo weather",
                "keys": [],
                "configured": True,
                "powers": "Forecast and climate risk for Agent 5",
                "without_it": "n/a - keyless public API",
            },
            {
                "id": "soilgrids",
                "name": "ISRIC SoilGrids",
                "keys": [],
                "configured": True,
                "powers": "Background soil properties for Agent 2",
                "without_it": "n/a - keyless public API",
            },
        ]


_settings: Settings | None = None
_lock = threading.Lock()


def get_settings(reload: bool = False) -> Settings:
    global _settings
    with _lock:
        if _settings is None or reload:
            _settings = Settings(values=_parse_env_file(CONFIG_FILE))
        return _settings


def ensure_dirs() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
