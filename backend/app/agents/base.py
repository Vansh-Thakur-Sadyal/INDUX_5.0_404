"""Agent base class and the shared context passed down the chain.

Agents are agricultural specialists. They are rule, calculation and retrieval
driven; the LLM is a shared capability outside the chain and is used for
language, not for deciding agronomy. That separation is what keeps the numbers
reproducible and auditable.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..core.schemas import (AgentResult, Evidence, FarmContext, Provenance,
                            RetrievedChunk, SensorReading, Severity, Status)


@dataclass
class AgentContext:
    """Everything an agent may read. Agents never fetch for themselves."""

    question: str = ""
    intent: str = "general"
    language: str = "en"
    entities: Dict[str, Any] = field(default_factory=dict)
    farm: FarmContext = field(default_factory=FarmContext)
    reading: Optional[SensorReading] = None
    reading_validation: Dict[str, Any] = field(default_factory=dict)
    weather: Dict[str, Any] = field(default_factory=dict)
    soilgrids: Dict[str, Any] = field(default_factory=dict)
    market: Dict[str, Any] = field(default_factory=dict)
    # Schemes, credit terms and helplines, when the question is near them.
    support: Dict[str, Any] = field(default_factory=dict)
    # What this farm has actually spent and earned, from the ledger.
    ledger: Dict[str, Any] = field(default_factory=dict)
    retrieved: List[RetrievedChunk] = field(default_factory=list)
    results: Dict[int, AgentResult] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------- helpers
    def result(self, agent_id: int) -> Optional[AgentResult]:
        return self.results.get(agent_id)

    def knowledge(self, *topics: str, limit: int = 3) -> List[RetrievedChunk]:
        """Retrieved chunks filtered by topic, falling back to the top hits."""
        if not self.retrieved:
            return []
        wanted = {t.lower() for t in topics}
        if wanted:
            matched = [c for c in self.retrieved
                       if wanted & {t.lower() for t in c.topics}]
            if matched:
                return matched[:limit]
        return self.retrieved[:limit]

    def evidence_from(self, *topics: str, limit: int = 3) -> List[Evidence]:
        return [
            Evidence(source=chunk.title, detail=chunk.source,
                     kind=Provenance.KNOWLEDGE_BASE, score=chunk.score)
            for chunk in self.knowledge(*topics, limit=limit)
        ]

    @property
    def has_usable_reading(self) -> bool:
        if self.reading is None:
            return False
        status = (self.reading_validation or {}).get("status")
        return status in ("valid", "partial", "degraded")

    @property
    def reading_is_simulated(self) -> bool:
        """Were these numbers measured, or generated?

        Asked of the PROVENANCE, not of a mode. With the simulator gone every reading
        is mode="real" - it arrived from a device or from the farmer - but a packet
        off an Arduino that has no soil probe wired in still carries generated values,
        and that has to stay visible. This is the check the validator and every
        agent warning hang off, so narrowing it would silently un-flag them.
        """
        if not self.reading:
            return False
        provenance = getattr(self.reading.provenance, "value", self.reading.provenance)
        return str(provenance) in ("simulated", "device_simulated")

    def value(self, field_name: str) -> Optional[float]:
        if not self.has_usable_reading or self.reading is None:
            return None
        return getattr(self.reading, field_name, None)

    def simulation_warning(self) -> List[str]:
        if self.reading_is_simulated:
            return ["These values arrived from the device, but the board is generating "
                    "them rather than reading soil - they are not measurements from "
                    "your field."]
        return []


class BaseAgent:
    """Subclasses implement ``analyse`` and return an AgentResult."""

    agent_id: int = 0
    name: str = "Agent"
    purpose: str = ""
    inputs: List[str] = []
    outputs: List[str] = []
    depends_on: List[int] = []

    async def analyse(self, ctx: AgentContext) -> AgentResult:  # pragma: no cover
        raise NotImplementedError

    async def run(self, ctx: AgentContext) -> AgentResult:
        started = time.perf_counter()
        try:
            result = await self.analyse(ctx)
        except Exception as exc:                                  # noqa: BLE001
            result = AgentResult(
                agent_id=self.agent_id,
                agent_name=self.name,
                status=Status.ERROR,
                headline="%s could not complete." % self.name,
                warnings=["Internal error: %s" % str(exc)[:200]],
                confidence=0.0,
            )
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    # --------------------------------------------------------- conveniences
    def blank(self, **kwargs: Any) -> AgentResult:
        base = {
            "agent_id": self.agent_id,
            "agent_name": self.name,
            "status": Status.OK,
            "severity": Severity.NONE,
            "confidence": 0.6,
        }
        base.update(kwargs)
        return AgentResult(**base)

    def unavailable(self, reason: str, missing: Optional[List[str]] = None,
                    questions: Optional[List[str]] = None) -> AgentResult:
        # Asking the farmer something is NEEDS_INPUT; a service we cannot reach is
        # UNAVAILABLE, because no answer from the farmer would fix it.
        return AgentResult(
            agent_id=self.agent_id,
            agent_name=self.name,
            status=Status.NEEDS_INPUT if questions else Status.UNAVAILABLE,
            headline=reason,
            missing_inputs=missing or [],
            questions=questions or [],
            provenance=[Provenance.UNAVAILABLE],
            confidence=0.0,
        )


def digest(results: List[AgentResult], max_chars: int = 2600) -> str:
    """Compact agent findings for the LLM prompt. Numbers are passed verbatim."""
    lines: List[str] = []
    for result in results:
        if result.status == Status.UNAVAILABLE and not result.findings:
            lines.append("Agent %d (%s): %s" % (result.agent_id, result.agent_name,
                                                result.headline))
            continue
        lines.append("Agent %d (%s) - %s" % (result.agent_id, result.agent_name,
                                             result.headline or result.status.value))
        for finding in result.findings[:4]:
            lines.append("   - " + finding)
        for action in result.actions[:3]:
            lines.append("   action: " + action)
        for warning in result.warnings[:2]:
            lines.append("   warning: " + warning)
        for question in result.questions[:2]:
            lines.append("   ask: " + question)
    text = "\n".join(lines)
    return text[:max_chars]
