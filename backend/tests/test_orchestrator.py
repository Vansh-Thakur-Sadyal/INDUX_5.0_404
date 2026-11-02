"""Orchestration: agent selection, dependency order, validation and composition."""
from __future__ import annotations

import pytest

from app.conversation import manager
from app.core import storage
from app.orchestrator import orchestrator
import reading_factory as simulator


def _session(language="en"):
    session = manager.load(None, language=language)
    session.farm.crop = "wheat"
    session.farm.sowing_date = "2026-11-10"
    manager.save(session)
    return session


def _store_reading(scenario="normal", seed=5, provenance=""):
    storage.add_reading(simulator.packet(scenario, seed=seed, provenance=provenance))


@pytest.mark.asyncio
async def test_only_relevant_agents_are_invoked():
    _store_reading()
    advisory = await orchestrator.run("What is the mandi price of wheat?", _session())
    assert set(advisory.agents_invoked) <= {12, 13, 15}
    assert 7 not in advisory.agents_invoked


@pytest.mark.asyncio
async def test_validation_agent_always_runs_last():
    _store_reading()
    advisory = await orchestrator.run("Should I irrigate today?", _session())
    assert advisory.agents_invoked[-1] == 15


@pytest.mark.asyncio
async def test_dependencies_run_before_their_dependents():
    _store_reading()
    advisory = await orchestrator.run("Should I irrigate today?", _session())
    order = advisory.agents_invoked
    assert order.index(4) < order.index(7)        # stage before irrigation
    assert order.index(5) < order.index(7)        # weather before irrigation


@pytest.mark.asyncio
async def test_unmeasured_sensor_data_reaches_the_advisory_warnings():
    """A board with no probe wired in: the packet is real, the values are generated,
    and the farmer has to be told before acting on them."""
    _store_reading(provenance="device_simulated")
    advisory = await orchestrator.run("How is my soil?", _session())
    assert any("not measurements" in w.lower() for w in advisory.warnings), advisory.warnings
    assert advisory.provenance_summary.get("device_simulated", 0) >= 1


@pytest.mark.asyncio
async def test_without_a_language_model_the_answer_is_labelled_rule_engine():
    _store_reading()
    advisory = await orchestrator.run("Should I irrigate today?", _session())
    assert advisory.answer_source == "rule_engine"
    assert any("Mistral API key is not configured" in w for w in advisory.warnings)
    assert advisory.answer


@pytest.mark.asyncio
async def test_with_a_language_model_the_answer_comes_from_it(fake_llm):
    _store_reading()
    advisory = await orchestrator.run("Should I irrigate today?", _session())
    assert advisory.answer_source == "llm"
    assert advisory.answer == "STUBBED ANSWER from the language model."
    chat_calls = [c for c in fake_llm if c["url"].endswith("/chat/completions")]
    assert chat_calls
    prompt = chat_calls[-1]["payload"]["messages"][-1]["content"]
    assert "AGENT FINDINGS" in prompt and "RETRIEVED KNOWLEDGE" in prompt


@pytest.mark.asyncio
async def test_the_language_directive_is_sent_for_a_regional_language(fake_llm):
    _store_reading()
    advisory = await orchestrator.run("Paani kab dena hai?", _session(language="hi"))
    system = fake_llm[-1]["payload"]["messages"][0]["content"]
    assert "Hindi" in system
    assert advisory.language == "hi"


@pytest.mark.asyncio
async def test_rag_can_be_switched_off():
    advisory = await orchestrator.run("How is my soil?", _session(), use_rag=False)
    assert advisory.retrieved == []


@pytest.mark.asyncio
async def test_agents_can_be_switched_off():
    advisory = await orchestrator.run("How is my soil?", _session(), run_agents=False)
    assert advisory.agents_invoked == []
    assert advisory.answer


@pytest.mark.asyncio
async def test_conversation_context_carries_the_crop_forward():
    _store_reading()
    session = _session()
    await orchestrator.run("Is mustard suitable for my field?", session)
    session = manager.load(session.session_id)
    assert session.memory.get("crop") == "mustard"
    advisory = await orchestrator.run("and what about its seed rate?", session)
    assert advisory.intent in ("seed_sowing", "crop_selection")


@pytest.mark.asyncio
async def test_every_advisory_is_persisted_with_its_trace():
    _store_reading()
    advisory = await orchestrator.run("Should I irrigate today?", _session())
    stored = storage.recent_advisories(5)
    assert stored and stored[0]["request_id"] == advisory.request_id
    assert stored[0]["agent_results"]


@pytest.mark.asyncio
async def test_follow_up_questions_are_surfaced():
    advisory = await orchestrator.run("there are spots", _session())
    assert advisory.follow_up_questions
