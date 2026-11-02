"""The registry of exactly 15 agricultural agents.

Conversational AI, the LLM, RAG, the orchestrator, the sensor transport and the
delivery layer are shared capabilities and are deliberately NOT in this list.
``assert_fifteen`` is called at import time and by the test-suite, because the
"exactly 15" property is part of the project's specification.
"""
from __future__ import annotations

from typing import Dict, List, Type

from .base import BaseAgent
from .crop import CropStageAgent, CropSuitabilityAgent, SeedSowingAgent
from .economy import EconomicsAgent, MarketAgent, ResidueAgent
from .environment import IrrigationAgent, NutrientAgent, WeatherRiskAgent
from .protection import CropProtectionAgent, PestDiseaseAgent, WeedAgent
from .soil import SensorAcquisitionAgent, SoilHealthAgent
from .validator import ValidationAgent

AGENT_CLASSES: List[Type[BaseAgent]] = [
    SensorAcquisitionAgent,     # 1
    SoilHealthAgent,            # 2
    CropSuitabilityAgent,       # 3
    CropStageAgent,             # 4
    WeatherRiskAgent,           # 5
    NutrientAgent,              # 6
    IrrigationAgent,            # 7
    PestDiseaseAgent,           # 8
    WeedAgent,                  # 9
    SeedSowingAgent,            # 10
    CropProtectionAgent,        # 11
    MarketAgent,                # 12
    EconomicsAgent,             # 13
    ResidueAgent,               # 14
    ValidationAgent,            # 15
]

AGENTS: Dict[int, BaseAgent] = {cls.agent_id: cls() for cls in AGENT_CLASSES}

SHARED_CAPABILITIES = [
    {"id": "conversational_ai", "name": "Conversational AI",
     "role": "Dialogue, follow-up questions, context and language handling"},
    {"id": "llm", "name": "LLM (Mistral)",
     "role": "Language understanding and response generation"},
    {"id": "rag", "name": "RAG retrieval",
     "role": "Retrieves agricultural knowledge and source references"},
    {"id": "orchestrator", "name": "Central orchestrator",
     "role": "Routes tasks, manages dependencies, collects outputs"},
    {"id": "sensor_transport", "name": "Sensor transport service",
     "role": "Moves readings from Arduino and BLE to the backend"},
    {"id": "delivery", "name": "Response delivery",
     "role": "Returns the validated response on the chosen channel"},
]


def assert_fifteen() -> None:
    ids = sorted(AGENTS)
    if ids != list(range(1, 16)):
        raise AssertionError("Agent registry must hold exactly agents 1-15, found %s" % ids)


assert_fifteen()


def get(agent_id: int) -> BaseAgent:
    return AGENTS[agent_id]


def describe() -> List[Dict[str, object]]:
    """Agent cards for the dashboard architecture page."""
    return [
        {
            "id": agent.agent_id,
            "name": agent.name,
            "purpose": agent.purpose,
            "inputs": agent.inputs,
            "outputs": agent.outputs,
            "depends_on": agent.depends_on,
        }
        for agent in sorted(AGENTS.values(), key=lambda a: a.agent_id)
    ]
