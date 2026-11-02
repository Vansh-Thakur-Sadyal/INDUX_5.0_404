"""Shared pydantic contracts.

Every agent returns an AgentResult. The orchestrator returns an Advisory.
Provenance is mandatory everywhere: a value that did not come from a verified
source is labelled, never promoted to a measurement.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Provenance(str, Enum):
    """Where a number came from; drives every trust decision downstream."""

    REAL_SENSOR = "real_sensor"
    # The packet really did arrive from the device over BLE, but the device is
    # generating the numbers itself because no soil probe is wired in yet. That is a
    # different claim from either "measured" or "simulated by us", and collapsing it
    # into one of those is how a demo figure ends up quoted as a measurement.
    DEVICE_SIMULATED = "device_simulated"
    SIMULATED = "simulated"
    LAB = "lab"
    LIVE_API = "live_api"
    KNOWLEDGE_BASE = "knowledge_base"
    FARMER_REPORTED = "farmer_reported"
    ESTIMATED = "estimated"
    UNAVAILABLE = "unavailable"


class Status(str, Enum):
    OK = "ok"
    NEEDS_INPUT = "needs_input"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class Severity(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Evidence(BaseModel):
    """A traceable reference behind a statement."""

    source: str
    detail: str = ""
    kind: Provenance = Provenance.KNOWLEDGE_BASE
    retrieved_at: Optional[datetime] = None
    score: Optional[float] = None
    url: Optional[str] = None


class AgentResult(BaseModel):
    agent_id: int
    agent_name: str
    status: Status = Status.OK
    headline: str = ""
    findings: List[str] = Field(default_factory=list)
    actions: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict)
    assumptions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    missing_inputs: List[str] = Field(default_factory=list)
    questions: List[str] = Field(default_factory=list)
    evidence: List[Evidence] = Field(default_factory=list)
    provenance: List[Provenance] = Field(default_factory=list)
    severity: Severity = Severity.NONE
    confidence: float = 0.5
    duration_ms: int = 0
    generated_at: datetime = Field(default_factory=utcnow)


class SensorReading(BaseModel):
    """The seven-parameter soil record, with provenance attached."""

    device_id: str = "unknown"
    parcel_id: str = "field-1"
    mode: str = "simulation"
    provenance: Provenance = Provenance.SIMULATED
    nitrogen_mg_kg: Optional[float] = None
    phosphorus_mg_kg: Optional[float] = None
    potassium_mg_kg: Optional[float] = None
    temperature_c: Optional[float] = None
    moisture_pct: Optional[float] = None
    ec_ds_m: Optional[float] = None
    ph: Optional[float] = None
    recorded_at: datetime = Field(default_factory=utcnow)
    received_at: datetime = Field(default_factory=utcnow)
    # Where the probe is standing. The NEO-6M fills these when it has a fix; the
    # farmer can also pick the spot on the map or type it in, and location_source
    # says which of those happened so a hand-typed point is never read as a GPS fix.
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_source: Optional[str] = None
    gps_satellites: Optional[int] = None
    gps_fix: Optional[bool] = None
    link: Optional[str] = None
    scenario: Optional[str] = None
    seed: Optional[int] = None
    raw_packet: Optional[str] = None
    validation: Dict[str, Any] = Field(default_factory=dict)


class FarmContext(BaseModel):
    farmer_name: str = "Farmer"
    phone: Optional[str] = None
    village: str = "Rampur"
    district: str = "Agra"
    state: str = "Uttar Pradesh"
    latitude: float = 27.1767
    longitude: float = 78.0081
    parcel_id: str = "field-1"
    area_acres: float = 2.0
    crop: Optional[str] = "wheat"
    variety: Optional[str] = None
    sowing_date: Optional[str] = None
    stage: Optional[str] = None
    season: str = "rabi"
    irrigation_method: str = "flood"
    water_availability: str = "moderate"
    soil_type: str = "alluvial"
    language: str = "en"


class RetrievedChunk(BaseModel):
    chunk_id: str
    doc_id: str
    title: str
    text: str
    score: float
    topics: List[str] = Field(default_factory=list)
    source: str = ""


class Advisory(BaseModel):
    """The orchestrator's unified output for one farmer request."""

    request_id: str
    session_id: str
    question: str
    language: str
    intent: str
    answer: str = ""
    answer_source: str = "rule_engine"
    answer_model: str = ""
    agents_invoked: List[int] = Field(default_factory=list)
    agent_results: List[AgentResult] = Field(default_factory=list)
    retrieved: List[RetrievedChunk] = Field(default_factory=list)
    validation: Dict[str, Any] = Field(default_factory=dict)
    follow_up_questions: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    provenance_summary: Dict[str, int] = Field(default_factory=dict)
    timings: Dict[str, int] = Field(default_factory=dict)
    duration_ms: int = 0
    generated_at: datetime = Field(default_factory=utcnow)


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    language: str = "en"
    use_rag: bool = True
    run_agents: bool = True
    speak: bool = False
    # True = the fast conversational engine with per-farmer memory (what the phone
    # line uses). False = the full 15-agent run with its trace.
    conversational: bool = False
    # True when the language was identified from the audio by Azure
    # rather than guessed from the words. The server then trusts it
    # outright instead of re-reading the script of the transcript.
    language_known: bool = False
    channel: str = "dashboard"
    farm: Optional[FarmContext] = None
    # Whose account this is. The dashboard sends the number saved on the profile, so
    # the assistant answers as that farmer - with their fields and their memory -
    # rather than as an anonymous guest whose memory nobody can find again.
    phone: Optional[str] = None
    # Which page they are looking at, so "what does this say" has a subject.
    # Saarthi cannot see the screen; it reads the data the screen was drawn
    # from, which is the same thing and more reliable.
    page: Optional[str] = None


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    language: str
    advisory: Optional[Advisory] = None
    audio_url: Optional[str] = None
    notices: List[str] = Field(default_factory=list)
    # Set when the farmer asked Saarthi to DO something rather than to answer.
    # ``ui`` is an instruction for whichever surface is listening - open a page,
    # run the chain, stop. Null on an ordinary question, which is most turns.
    action: str = ""
    ui: Optional[Dict[str, Any]] = None
    awaiting_confirmation: bool = False


class CallRequest(BaseModel):
    # Optional since accounts: a call-back goes to the number the account signed up
    # with, so the dashboard has nothing to send and nothing to get wrong. The field
    # remains for the n8n and admin paths, which name the number explicitly.
    phone: str = ""
    language: str = "hi"
    topic: str = ""
    # What Saarthi should SAY when the line opens, for a call it placed with
    # something to deliver. A booked call that greets the farmer and waits is a
    # call that never said why it rang.
    opening: str = ""
    farmer_name: str = "Farmer"
    simulate: bool = False


class CallTurnIn(BaseModel):
    call_sid: str
    transcript: str = ""
    language: Optional[str] = None
    digits: Optional[str] = None
    phone: Optional[str] = None
