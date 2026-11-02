"""Conversational AI layer.

This is the dialogue capability from the write-up: turn taking, context,
language handling and follow-up questions. It is deliberately NOT one of the 15
agricultural agents - it only decides what the farmer is asking and keeps the
thread of the conversation. The orchestrator does the agricultural work.

State lives in SQLite so a conversation survives a restart and so a farmer who
calls back can be resumed once their identity is known.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..core import languages, storage
from ..core.schemas import FarmContext
from . import intent as intent_module

MAX_HISTORY_TURNS = 12
# Words that mean "the thing we were just talking about".
_REFERENCE_WORDS = ("it", "that", "this", "same", "usi", "wahi", "uske", "iske")


@dataclass
class Session:
    session_id: str
    language: str
    channel: str
    farm: FarmContext
    memory: Dict[str, Any]
    history: List[Dict[str, Any]]

    @property
    def last_crop(self) -> Optional[str]:
        return self.memory.get("crop") or self.farm.crop

    @property
    def pending_question(self) -> Optional[str]:
        return self.memory.get("pending_question")


def new_session_id(prefix: str = "web") -> str:
    return prefix + "-" + uuid.uuid4().hex[:12]


def load(session_id: Optional[str], *, language: str = "en", channel: str = "dashboard",
         farm: Optional[FarmContext] = None, phone: Optional[str] = None) -> Session:
    """Fetch or create a session. Never fails: a bad id just starts a new thread."""
    sid = session_id or new_session_id("web" if channel == "dashboard" else channel)
    row = storage.get_session(sid)
    if row is None:
        farm_ctx = farm or FarmContext(language=language, phone=phone)
        farm_ctx.language = language
        if phone:
            farm_ctx.phone = phone
        storage.upsert_session(
            sid, channel=channel, language=language, phone=phone,
            farm=farm_ctx.model_dump(mode="json"), memory={},
        )
        return Session(sid, language, channel, farm_ctx, {}, [])

    stored_farm = row.get("farm") or {}
    if farm is not None:
        stored_farm.update(farm.model_dump(mode="json", exclude_none=True))
    try:
        farm_ctx = FarmContext(**stored_farm)
    except Exception:                                        # noqa: BLE001 - never block a chat
        farm_ctx = FarmContext()
    chosen_language = language or row.get("language") or "en"
    farm_ctx.language = chosen_language
    if phone:
        farm_ctx.phone = phone
    return Session(
        session_id=sid,
        language=chosen_language,
        channel=channel or row.get("channel", "dashboard"),
        farm=farm_ctx,
        memory=row.get("memory") or {},
        history=storage.get_turns(sid, MAX_HISTORY_TURNS),
    )


def language_of(session_id: Optional[str]) -> str:
    """The language a conversation is already being held in, or "".

    Read without loading the whole session, because the one caller wants it
    BEFORE the session exists as an object: it is deciding what language to
    load it in.
    """
    if not session_id:
        return ""
    row = storage.get_session(session_id)
    return str((row or {}).get("language") or "")


def save(session: Session) -> None:
    storage.upsert_session(
        session.session_id,
        channel=session.channel,
        language=session.language,
        phone=session.farm.phone,
        farm=session.farm.model_dump(mode="json"),
        memory=session.memory,
    )


def record_user_turn(session: Session, text: str, intent_name: str = "") -> None:
    storage.add_turn(session.session_id, "user", text, session.language, intent_name)


def record_assistant_turn(session: Session, text: str, meta: Optional[Dict[str, Any]] = None,
                          intent_name: str = "") -> None:
    storage.add_turn(session.session_id, "assistant", text, session.language,
                     intent_name, meta or {})


def history_for_prompt(session: Session) -> List[Dict[str, str]]:
    return [
        {"role": turn["role"], "content": turn["content"]}
        for turn in storage.get_turns(session.session_id, 8)
    ]


def resolve_turn(session: Session, text: str) -> intent_module.IntentResult:
    """Detect intent for this turn, carrying context forward from earlier turns."""
    result = intent_module.detect(text)

    # "what about it" - reuse the previous intent when the turn is a bare follow-up.
    lowered = (text or "").lower()
    is_reference = any(word in lowered.split() for word in _REFERENCE_WORDS)
    if (result.intent == "general" or result.confidence < 0.5) and session.memory.get("intent"):
        if is_reference or len(lowered.split()) <= 5:
            previous = session.memory["intent"]
            spec = intent_module.INTENTS.get(previous)
            if spec:
                result.intent = previous
                result.topics = list(spec["topics"])          # type: ignore[arg-type]
                result.agents = list(spec["agents"])          # type: ignore[arg-type]
                result.confidence = max(result.confidence, 0.55)
                result.entities.setdefault("carried_from", previous)

    # Carry the crop forward if this turn did not name one.
    if "crop" not in result.entities and session.last_crop:
        result.entities["crop"] = session.last_crop
        result.entities["crop_source"] = "remembered"
    return result


# Intents where a named crop is the SUBJECT of the question, not what the farmer
# grows. "Maharashtra mein pyaaz ka bhav kya hai" names an onion and means
# nothing about their field - but it used to set the farm crop to onion, and the
# next turn asked "aapka pyaaz ka crop kaisa chal raha hai?" of a wheat farmer.
ASKING_ABOUT_SOMEONE_ELSES_CROP = {"market", "economics"}


def update_memory(session: Session, result: intent_module.IntentResult,
                  answered: bool = True) -> None:
    session.memory["intent"] = result.intent
    if (result.entities.get("crop")
            and result.entities.get("crop_source") != "remembered"
            and result.intent not in ASKING_ABOUT_SOMEONE_ELSES_CROP):
        session.memory["crop"] = result.entities["crop"]
        session.farm.crop = result.entities["crop"]
    if result.entities.get("stage"):
        session.memory["stage"] = result.entities["stage"]
        session.farm.stage = result.entities["stage"]
    if answered:
        session.memory.pop("pending_question", None)
    session.memory["turns"] = int(session.memory.get("turns", 0)) + 1


def set_pending_question(session: Session, question: str) -> None:
    session.memory["pending_question"] = question


def set_language(session: Session, code: str) -> str:
    lang = languages.resolve(code)
    session.language = lang.code
    session.farm.language = lang.code
    save(session)
    return lang.code


def summary(session: Session) -> Dict[str, Any]:
    return {
        "session_id": session.session_id,
        "language": session.language,
        "channel": session.channel,
        "turns": int(session.memory.get("turns", 0)),
        "remembered_crop": session.last_crop,
        "remembered_stage": session.memory.get("stage"),
        "last_intent": session.memory.get("intent"),
        "pending_question": session.pending_question,
        "farm": session.farm.model_dump(mode="json"),
    }
