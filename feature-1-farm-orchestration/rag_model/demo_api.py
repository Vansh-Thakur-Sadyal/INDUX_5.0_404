"""Minimal API for trying this RAG package outside the farm dashboard."""

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .assistant import answer_question


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    crop: str | None = None
    location: str | None = None
    lat: float | None = None
    lon: float | None = None
    farm_context: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(title="Farm RAG Demo", version="1.0.0")


@app.post("/chat")
def chat(request: ChatRequest) -> dict[str, str]:
    body = request.model_dump()
    try:
        return answer_question(request.message.strip(), body=body)
    except RuntimeError as error:
        raise HTTPException(
            status_code=503,
            detail="The model is unavailable. Check MISTRAL_API_KEY and try again.",
        ) from error
