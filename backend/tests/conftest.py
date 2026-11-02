"""Shared fixtures.

Every test runs fully offline: external services are replaced by deterministic
stubs, and the database is a throwaway file per test. That means the suite
proves the pipeline's logic, not the weather.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import httpx
import importlib
import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core import config, storage                                   # noqa: E402
from app.llm import mistral_client, sarvam_client                             # noqa: E402
from app.rag import pipeline as rag_pipeline                   # noqa: E402
from app.rag.store import VectorStore                          # noqa: E402
from app.rag import store as store_module                      # noqa: E402
from app.speech import azure_speech, voice_io                                 # noqa: E402
from app.services import http_cache                            # noqa: E402
from app.telephony import n8n_client                           # noqa: E402

WEATHER_FIXTURE: Dict[str, Any] = {
    "timezone": "Asia/Kolkata",
    "current": {"temperature_2m": 27.5, "relative_humidity_2m": 62, "precipitation": 0.0,
                "weather_code": 1, "wind_speed_10m": 8.0, "time": "2026-01-20T10:00"},
    "daily": {
        "time": ["2026-01-20", "2026-01-21", "2026-01-22", "2026-01-23", "2026-01-24"],
        "weather_code": [1, 2, 61, 3, 0],
        "temperature_2m_max": [28.0, 29.5, 26.0, 27.0, 30.0],
        "temperature_2m_min": [12.0, 13.5, 14.0, 12.5, 11.0],
        "precipitation_sum": [0.0, 0.4, 3.2, 0.0, 0.0],
        "precipitation_probability_max": [5, 15, 60, 20, 5],
        "wind_speed_10m_max": [10.0, 12.0, 18.0, 9.0, 7.0],
        "et0_fao_evapotranspiration": [3.1, 3.4, 2.2, 3.0, 3.6],
    },
}

MARKET_FIXTURE: Dict[str, Any] = {
    "records": [
        {"market": "Agra", "district": "Agra", "state": "Uttar Pradesh", "variety": "Dara",
         "arrival_date": "19/01/2026", "min_price": "2200", "max_price": "2450", "modal_price": "2325"},
        {"market": "Delhi", "district": "Delhi", "state": "Delhi", "variety": "Dara",
         "arrival_date": "19/01/2026", "min_price": "2300", "max_price": "2500", "modal_price": "2410"},
        {"market": "Jaipur", "district": "Jaipur", "state": "Rajasthan", "variety": "Dara",
         "arrival_date": "18/01/2026", "min_price": "2150", "max_price": "2400", "modal_price": "2280"},
    ]
}

SOILGRIDS_FIXTURE: Dict[str, Any] = {
    "properties": {"layers": [
        {"name": "phh2o", "depths": [{"label": "0-5cm", "values": {"mean": 72}}]},
        {"name": "soc", "depths": [{"label": "0-5cm", "values": {"mean": 61}}]},
        {"name": "clay", "depths": [{"label": "0-5cm", "values": {"mean": 240}}]},
    ]}
}


def _fake_fetch(url: str, params: Dict[str, Any]):
    if "open-meteo" in url:
        return WEATHER_FIXTURE
    if "data.gov.in" in url:
        return MARKET_FIXTURE
    if "isric" in url:
        return SOILGRIDS_FIXTURE
    return None


class _BlockedAsyncClient:
    """Guard: any unstubbed outbound HTTP call in a test is a broken test.

    Installed over httpx.AsyncClient, which the LLM client, Azure Speech and the
    n8n client all use. Note it must NOT be done by installing a fake LLM
    transport instead: the client treats "a transport is installed" as "the key
    is configured", which would silently defeat the no-key tests.
    """

    def __init__(self, *args, **kwargs):
        raise AssertionError(
            "A test tried to open a real HTTP connection. Use the fake_llm fixture, "
            "http_cache.set_fetcher or n8n_client.set_poster instead.")


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Fresh database, stubbed network, offline embeddings, no real telephony."""
    storage.init(tmp_path / "test.db", reset=True)
    http_cache.set_fetcher(_fake_fetch)
    http_cache.clear_cache()
    monkeypatch.setattr(httpx, "AsyncClient", _BlockedAsyncClient)

    # A private vector store per test so indexes never leak between tests.
    #
    # Every module that holds a reference, not just two of them. Four modules do
    # `from .store import store`, which binds the OBJECT at import time, so
    # patching the two that were listed here left `ingest` and `chain` writing
    # to the real shared index - which is why `data/storage/vector_index.json`
    # was being modified by test runs, and why a document a test had just
    # ingested could not be found by a search in the same test.
    test_store = VectorStore(tmp_path / "index.json")
    monkeypatch.setattr(store_module, "store", test_store)
    for holder in ("app.rag.pipeline", "app.rag.ingest", "app.rag.chain",
                   "app.rag.documents"):
        monkeypatch.setattr(importlib.import_module(holder), "store", test_store)

    # An empty environment variable does NOT hide a value in config/api_keys.env -
    # Settings falls through to the file. Once a real key lives there, the suite would
    # silently start calling the live API (and the "no LLM configured" tests would
    # fail). So blank the loaded values themselves.
    settings = config.get_settings(reload=True)
    for key in ("MISTRAL_API_KEY", "AZURE_SPEECH_KEY", "SARVAM_API_KEY",
                "N8N_BASE_URL", "N8N_WEBHOOK_TOKEN", "MONGODB_URI",
                "SAARTHI_HELPLINE_NUMBER"):
        settings.values[key] = ""
    settings.values["DATA_GOV_API_KEY"] = "test-key"          # market stub is served offline
    settings.values["SAARTHI_INBOUND_TOKEN"] = "test-secret"
    for key in ("MISTRAL_API_KEY", "AZURE_SPEECH_KEY", "SARVAM_API_KEY",
                "N8N_BASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("DATA_GOV_API_KEY", "test-key")
    monkeypatch.setenv("SAARTHI_INBOUND_TOKEN", "test-secret")

    # Synthesised audio is cached on disk by a digest of the words, and that cache is
    # NOT under the per-test database. Left alone, the suite reads and writes the real
    # data/storage/audio: a cache-behaviour test would pass on a clip some earlier run
    # had left behind, and the tests would litter real storage. Give each test its own.
    monkeypatch.setattr(voice_io, "AUDIO_DIR", tmp_path / "audio")
    monkeypatch.setattr(azure_speech, "AUDIO_DIR", tmp_path / "audio")   # the serving side

    yield

    config.get_settings(reload=True)                          # restore the real file

    http_cache.set_fetcher(None)
    mistral_client.set_transport(None)
    sarvam_client.set_transport(None)
    n8n_client.set_poster(None)


@pytest.fixture
def fake_llm(monkeypatch):
    """Install a deterministic Mistral transport and return the captured calls."""
    calls = []

    def transport(url: str, payload: Dict[str, Any]):
        calls.append({"url": url, "payload": payload})
        if url.endswith("/embeddings"):
            return {"data": [{"index": i, "embedding": [0.01 * (i + 1)] * 1024}
                             for i in range(len(payload["input"]))]}
        return {"model": payload["model"], "usage": {"total_tokens": 42},
                "choices": [{"message": {"role": "assistant",
                                         "content": "STUBBED ANSWER from the language model."}}]}

    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral-key")
    mistral_client.set_transport(transport)
    yield calls
    mistral_client.set_transport(None)


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def farm():
    from app.core.schemas import FarmContext
    return FarmContext(crop="wheat", sowing_date="2025-11-10", area_acres=2.0)


@pytest.fixture
def profile_code(client):
    """A fresh code for confirming a profile change.

    Saving a profile needs one - the phone number on it decides which account a
    caller reaches, and the village and district decide whose weather and whose
    mandi prices are shown. Tests that are about profile STORAGE should not each
    have to re-derive how codes are issued, so they ask for one here.
    """
    from app.core import accounts as accounts_module

    def issue(headers=None) -> str:
        accounts_module.ensure_schema()
        # Whoever this client is signed in as - the check is on the SESSION, not
        # on the phone number in the body, so the code has to belong to the same
        # account the save will be attributed to.
        me = client.get("/api/auth/me", headers=headers or {}).json()
        user_id = (me.get("user") or {}).get("user_id") or me.get("user_id")
        assert user_id, "not signed in, so no account to send a code to: %r" % me
        return accounts_module.issue_otp(user_id, "profile", "sms", "")["code"]

    return issue


@pytest.fixture
def account(client):
    """An active, signed-in farmer, and the headers to act as them.

    Most data routes now require an account, so the tests that predate accounts use
    this rather than each inventing its own sign-in. Returns an object with
    ``phone``, ``token``, ``headers`` and ``admin_headers``.
    """
    from app.core import accounts as accounts_module

    class Account:
        phone = "9811100001"
        email = "farmer@example.com"
        password = "test-pass-1234"

    accounts_module.ensure_schema()
    user = accounts_module.create_user(email=Account.email, phone=Account.phone,
                                       password=Account.password, status="active")
    admin = accounts_module.create_user(email="root@example.com", phone="9811100099",
                                        password=Account.password, role="admin",
                                        status="active")
    Account.user_id = user["user_id"]
    Account.token = accounts_module.start_session(user["user_id"])
    Account.headers = {"Authorization": "Bearer " + Account.token}
    Account.admin_token = accounts_module.start_session(admin["user_id"])
    Account.admin_headers = {"Authorization": "Bearer " + Account.admin_token}
    return Account


@pytest.fixture
def solve_captcha(client):
    """Answer the challenge the public auth routes now require.

    Returns a dict to merge into the request body. Tests care about sign-up and
    sign-in behaviour, not about arithmetic, so this does what a person would.
    """
    def solve():
        from app.core import captcha as _captcha
        issued = client.get("/api/auth/captcha").json()
        words = _captcha.WORDS
        numbers = [words.index(w) for w in issued["question"].lower()
                   .replace("?", "").split() if w in words]
        return {"challenge_id": issued["challenge_id"], "captcha": str(sum(numbers))}
    return solve
