"""RAG and agent-architecture endpoints."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from ..agents import registry
from ..conversation import intent as intent_module
from ..conversation import manager
from ..orchestrator import orchestrator
from ..rag import pipeline
from ..rag.store import store

router = APIRouter(prefix="/api", tags=["knowledge"])


@router.get("/rag/stats")
async def rag_stats() -> Dict[str, Any]:
    await pipeline.ensure_index()
    stats = store.stats()
    stats["documents_on_disk"] = len(pipeline.load_documents())
    return stats


@router.post("/rag/search")
async def rag_search(payload: Dict[str, Any]) -> Dict[str, Any]:
    query = str(payload.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query is required")
    top_k = int(payload.get("top_k", 5))
    topics = payload.get("topics")
    normalised = intent_module.normalise_query(query)
    chunks = await pipeline.retrieve(normalised, top_k=top_k, topics=topics)
    return {
        "query": query,
        "normalised_query": normalised,
        "provider": store.provider_name or "offline",
        "results": [c.model_dump(mode="json") for c in chunks],
    }


@router.post("/rag/reindex")
async def rag_reindex(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rebuild = bool((payload or {}).get("rebuild", True))
    return await pipeline.ingest(rebuild=rebuild)


@router.get("/agents")
async def agents() -> Dict[str, Any]:
    registry.assert_fifteen()
    return {
        "count": len(registry.AGENTS),
        "agents": registry.describe(),
        "shared_capabilities": registry.SHARED_CAPABILITIES,
        "note": "Conversational AI, the LLM, RAG, the orchestrator, the sensor transport "
                "and response delivery are shared capabilities, not agents.",
    }


@router.get("/agents/routing")
async def routing() -> Dict[str, Any]:
    """Which agents each intent invokes - the orchestrator's routing table."""
    return {
        "intents": [
            {"intent": name,
             "agents": spec["agents"],
             "topics": spec["topics"],
             "example_keywords": list(spec["keywords"])[:6]}
            for name, spec in intent_module.INTENTS.items()
        ]
    }


@router.post("/agents/run")
async def run_agents(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Run the pipeline for a question and return the full agent trace."""
    question = str(payload.get("question", "")).strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    session = manager.load(payload.get("session_id"),
                           language=str(payload.get("language", "en")),
                           channel="dashboard")
    advisory = await orchestrator.run(
        question, session,
        use_rag=bool(payload.get("use_rag", True)),
        run_agents=bool(payload.get("run_agents", True)),
    )
    return advisory.model_dump(mode="json")


@router.post("/intent")
async def detect_intent(payload: Dict[str, Any]) -> Dict[str, Any]:
    text = str(payload.get("text", ""))
    result = intent_module.detect(text)
    return {
        "intent": result.intent,
        "confidence": result.confidence,
        "agents": result.agents,
        "topics": result.topics,
        "entities": result.entities,
        "normalised_query": result.normalised_query,
        "urgent": result.urgent,
        "scores": result.scores,
    }
