"""Mistral client behaviour under this account's real constraints.

Both tests here exist because of bugs found on 2026-10-13, when a working key was
first plugged in: this Mistral tier answers 429 for mistral-small-latest and 403
for mistral-large-latest, while the ministral models answer normally.
"""
from __future__ import annotations

import pytest

from app.llm import mistral_client
from app.llm.mistral_client import TierLimitError, client as llm
from app.rag import pipeline
from app.rag.embeddings import OFFLINE_DIMS, provider


def _embedding_response(payload):
    return {"data": [{"index": i, "embedding": [0.01 * (i + 1)] * 1024}
                     for i in range(len(payload["input"]))]}


@pytest.mark.asyncio
async def test_chat_falls_back_to_a_smaller_model_when_rate_limited(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("MISTRAL_MODEL", "mistral-small-latest")
    monkeypatch.setenv("MISTRAL_FALLBACK_MODEL", "ministral-3b-latest")
    tried = []

    def transport(url, payload):
        tried.append(payload["model"])
        if payload["model"] == "mistral-small-latest":
            raise TierLimitError("HTTP 429: Rate limit exceeded")
        return {"model": payload["model"],
                "choices": [{"message": {"content": "Answer from the fallback model."}}]}

    mistral_client.set_transport(transport)
    result = await llm.chat([{"role": "user", "content": "hello"}])

    assert result.ok is True
    assert result.text == "Answer from the fallback model."
    assert tried == ["mistral-small-latest", "ministral-3b-latest"]
    assert result.model == "ministral-3b-latest"      # reports what actually answered


@pytest.mark.asyncio
async def test_chat_walks_the_whole_fallback_chain(monkeypatch):
    """This tier also answers 'Not enough capacity' per model, so one spare is not enough."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("MISTRAL_MODEL", "ministral-14b-latest")
    monkeypatch.setenv("MISTRAL_FALLBACK_MODEL", "ministral-8b-latest, ministral-3b-latest")
    tried = []

    def transport(url, payload):
        tried.append(payload["model"])
        if payload["model"] != "ministral-3b-latest":
            raise TierLimitError("HTTP 429: Not enough capacity available")
        return {"model": payload["model"], "choices": [{"message": {"content": "Answer."}}]}

    mistral_client.set_transport(transport)
    result = await llm.chat([{"role": "user", "content": "hello"}])

    assert result.ok is True
    assert tried == ["ministral-14b-latest", "ministral-8b-latest", "ministral-3b-latest"]


@pytest.mark.asyncio
async def test_chat_gives_up_honestly_when_the_fallback_is_also_blocked(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    monkeypatch.setenv("MISTRAL_MODEL", "mistral-large-latest")
    monkeypatch.setenv("MISTRAL_FALLBACK_MODEL", "mistral-large-latest")

    def transport(url, payload):
        raise TierLimitError("HTTP 403: tier_not_allowed")

    mistral_client.set_transport(transport)
    result = await llm.chat([{"role": "user", "content": "hello"}])

    assert result.ok is False
    assert "403" in result.reason
    assert result.text == ""                           # no invented answer


@pytest.mark.asyncio
async def test_one_failed_embedding_call_does_not_latch_the_provider_to_offline(monkeypatch):
    """A single rate-limited embedding used to downgrade RAG until a restart."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    calls = {"n": 0}

    def transport(url, payload):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("HTTP 429: Rate limit exceeded")
        return _embedding_response(payload)

    mistral_client.set_transport(transport)

    vectors, used = await provider.embed_with_provider(["soil moisture"])
    assert used == "offline" and len(vectors[0]) == OFFLINE_DIMS

    vectors, used = await provider.embed_with_provider(["soil moisture"])
    assert used == "mistral" and len(vectors[0]) == 1024
    assert provider.name == "mistral"


@pytest.mark.asyncio
async def test_the_index_records_the_provider_that_really_embedded_it(monkeypatch):
    """With a key present but embeddings failing, the index must say 'offline'."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    def failing_transport(url, payload):
        raise RuntimeError("HTTP 429: Rate limit exceeded")

    mistral_client.set_transport(failing_transport)
    stats = await pipeline.ingest(rebuild=True)

    # pipeline.store is the per-test index installed by conftest, not the global one.
    assert stats["provider"] == "offline"
    assert stats["dims"] == OFFLINE_DIMS
    assert pipeline.store.provider_name == "offline"
    # ...and because the label is honest, the next run knows it must re-index.
    mistral_client.set_transport(lambda url, payload: _embedding_response(payload))
    assert pipeline.store.is_stale() is True
    stats = await pipeline.ensure_index()
    assert stats["provider"] == "mistral" and stats["dims"] == 1024
