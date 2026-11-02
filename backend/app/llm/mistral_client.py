"""Mistral client: chat completions and embeddings.

Design rules
------------
* Never fabricate. If the key is missing or the API fails, callers get a
  structured "unavailable" result and the UI says so. We do not fall back to a
  canned answer that looks like real advice.
* Rate limits are expected on free Mistral keys, so 429/5xx are retried with
  backoff a small number of times.
* A pluggable transport (``set_transport``) lets the test-suite run the whole
  pipeline deterministically without network access.
* Every request goes through one pooled connection (``llm.pool``). Opening a
  fresh TLS connection per call cost a measured 265 ms of every answer.
* ``stream()`` yields the answer as it is generated. The model takes ~2.1 s to
  finish a dashboard answer but produces its first words in ~550 ms, and those
  first words are what makes the reply feel immediate.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import (Any, AsyncIterator, Callable, Dict, List, Optional,
                    Sequence)

import httpx

from ..core.config import get_settings
from ..core import languages
from . import pool

log = logging.getLogger("saarthi.llm")

API_BASE = "https://api.mistral.ai/v1"
CHAT_URL = API_BASE + "/chat/completions"
EMBED_URL = API_BASE + "/embeddings"

# Transport override used by tests: async fn(url, payload) -> dict
Transport = Callable[[str, Dict[str, Any]], Any]
_transport: Optional[Transport] = None

# Embedding one query is a full network round trip, and the same question is asked
# over and over - by the retry button, by the console, by two farmers in the same
# village. Vectors for a given text never change, so they are worth keeping.
_EMBED_CACHE_MAX = 2048
_embed_cache: "OrderedDict[str, List[float]]" = OrderedDict()


def _embed_key(model: str, text: str) -> str:
    return model + "|" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def clear_embed_cache() -> None:
    _embed_cache.clear()


def set_transport(transport: Optional[Transport]) -> None:
    """Install (or clear) a fake transport. Tests only.

    Clearing the embedding cache is part of it: vectors cached from the real API
    would otherwise be served to a test that installed a transport specifically to
    make embedding fail, and the index would record a provider that never ran.
    """
    global _transport
    _transport = transport
    clear_embed_cache()


class TierLimitError(RuntimeError):
    """The account cannot use this model right now: rate-limited or off-tier.

    Worth its own type because the fix is to try a smaller model, not to retry
    the same one harder. On this account mistral-small-latest answers 429 and
    mistral-large-latest answers 403, while the ministral models work.
    """


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    reason: str = ""
    model: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)
    latency_ms: int = 0

    @property
    def unavailable_notice(self) -> str:
        return self.reason or "The language model is not available right now."


class MistralClient:
    def __init__(self, timeout: float = 45.0, max_retries: int = 3) -> None:
        self.timeout = timeout
        self.max_retries = max_retries

    # ------------------------------------------------------------- helpers
    @property
    def settings(self):
        return get_settings()

    def configured(self) -> bool:
        return self.settings.has("MISTRAL_API_KEY") or _transport is not None

    async def _post(self, url: str, payload: Dict[str, Any], *, urgent: bool = False,
                    timeout: Optional[float] = None) -> Dict[str, Any]:
        if _transport is not None:
            result = _transport(url, payload)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        headers = {
            "Authorization": "Bearer " + self.settings.mistral_api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # A person waiting for a spoken reply is not helped by a 1.5 s backoff and a
        # second attempt - by then the line has gone quiet. Urgent calls try once and
        # report the failure, so the caller can fall back to something it already has.
        attempts = 1 if urgent else self.max_retries
        delay = 1.5
        last_error = ""
        client = await pool.client(self.timeout)
        for attempt in range(1, attempts + 1):
            try:
                resp = await client.post(url, json=payload, headers=headers,
                                         timeout=timeout or self.timeout)
            except httpx.HTTPError as exc:                      # network level
                last_error = "network error: " + str(exc)[:160]
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code == 403:
                    raise TierLimitError(
                        "Model not available in this subscription tier (HTTP 403): "
                        + resp.text[:140])
                if resp.status_code == 401:
                    raise PermissionError(
                        "Mistral rejected the API key (HTTP 401). Update MISTRAL_API_KEY "
                        "in config/api_keys.env.")
                last_error = "HTTP %s: %s" % (resp.status_code, resp.text[:160])
                if resp.status_code not in (408, 409, 429, 500, 502, 503, 504):
                    break
                if resp.status_code == 429 and attempt == attempts:
                    raise TierLimitError(last_error)
            if attempt < attempts:
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError(last_error or "Mistral request failed")

    # ---------------------------------------------------------------- chat
    async def chat(self, messages: Sequence[Dict[str, str]], *, temperature: float = 0.2,
                   max_tokens: int = 700, json_mode: bool = False,
                   model: Optional[str] = None, urgent: bool = False,
                   timeout: Optional[float] = None) -> LLMResult:
        if not self.configured():
            return LLMResult(
                ok=False,
                reason=("Mistral API key is not configured, so no generated answer is "
                        "available. Add MISTRAL_API_KEY to config/api_keys.env."),
            )
        payload: Dict[str, Any] = {
            "model": model or self.settings.mistral_model,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        # The chosen model being rate-limited or off-tier is not fixed by retrying it
        # harder - a smaller model is the fix. The result reports which one answered.
        chain = [payload["model"]] + [m for m in self.settings.mistral_fallback_models
                                      if m != payload["model"]]
        data = None
        last_reason = ""
        for index, model_name in enumerate(chain):
            payload["model"] = model_name
            try:
                data = await self._post(CHAT_URL, payload, urgent=urgent, timeout=timeout)
                break
            except TierLimitError as exc:
                last_reason = str(exc)[:220]
                if index + 1 < len(chain):
                    log.info("Model %s unavailable (%s); trying %s",
                             model_name, last_reason[:80], chain[index + 1])
                    continue
                log.warning("Mistral chat unavailable on every model: %s", last_reason)
            except Exception as exc:                                 # noqa: BLE001
                log.warning("Mistral chat failed: %s", exc)
                last_reason = str(exc)[:220]
            break
        if data is None:
            return LLMResult(ok=False, reason=last_reason or "Mistral request failed")
        text = ""
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return LLMResult(ok=False, reason="Unexpected response shape from Mistral.")
        return LLMResult(
            ok=True,
            text=text.strip(),
            model=str(data.get("model", payload["model"])),
            usage=data.get("usage", {}) or {},
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


    # -------------------------------------------------------------- streaming
    async def stream(self, messages: Sequence[Dict[str, str]], *, temperature: float = 0.2,
                     max_tokens: int = 700, model: Optional[str] = None,
                     timeout: Optional[float] = None) -> AsyncIterator[Dict[str, Any]]:
        """Yield the answer as the model writes it.

        Each item is one of:
          {"delta": "..."}                    a piece of text, in order
          {"done": True, "text": ..., "model": ..., "latency_ms": ...,
           "first_token_ms": ...}
          {"error": "..."}                    nothing usable was produced

        Measured against ministral-8b-latest from this machine: the complete answer
        takes about 2.1 s, the first token arrives in about 550 ms. A dashboard that
        waits for the whole thing feels three times slower than the model really is.

        A test transport has no stream to read, so it is answered in one piece - the
        shape of the output is the same either way.
        """
        if not self.configured():
            yield {"error": ("Mistral API key is not configured, so no generated answer "
                             "is available.")}
            return

        chosen = model or self.settings.mistral_model
        started = time.perf_counter()

        if _transport is not None:                       # tests: one chunk, same shape
            result = await self.chat(messages, temperature=temperature,
                                     max_tokens=max_tokens, model=chosen)
            if not result.ok:
                yield {"error": result.reason}
                return
            yield {"delta": result.text}
            yield {"done": True, "text": result.text, "model": result.model,
                   "latency_ms": result.latency_ms, "first_token_ms": result.latency_ms}
            return

        payload: Dict[str, Any] = {
            "model": chosen,
            "messages": list(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        headers = {
            "Authorization": "Bearer " + self.settings.mistral_api_key,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        # The fallback chain matters here too: a 429 on the first model should move to
        # the next one rather than leave the page holding half a sentence.
        chain = [chosen] + [m for m in self.settings.mistral_fallback_models if m != chosen]
        last_error = ""
        for index, model_name in enumerate(chain):
            payload["model"] = model_name
            pieces: List[str] = []
            first_token_ms = 0
            answered = False
            try:
                client = await pool.client(self.timeout)
                async with client.stream("POST", CHAT_URL, json=payload, headers=headers,
                                         timeout=timeout or self.timeout) as response:
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", "replace")[:160]
                        last_error = "HTTP %s: %s" % (response.status_code, body)
                        if response.status_code in (403, 429) and index + 1 < len(chain):
                            log.info("Model %s unavailable while streaming (%s); trying %s",
                                     model_name, last_error[:70], chain[index + 1])
                            continue
                        break
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        body = line[5:].strip()
                        if not body or body == "[DONE]":
                            continue
                        try:
                            event = json.loads(body)
                        except json.JSONDecodeError:
                            continue
                        try:
                            delta = event["choices"][0]["delta"].get("content") or ""
                        except (KeyError, IndexError, TypeError):
                            continue
                        if not delta:
                            continue
                        if not answered:
                            answered = True
                            first_token_ms = int((time.perf_counter() - started) * 1000)
                        pieces.append(delta)
                        yield {"delta": delta}
            except httpx.HTTPError as exc:
                last_error = "network error: " + str(exc)[:160]
                # Half a sentence has already reached the page. Saying so is better
                # than letting it stand there looking like the whole answer.
                if pieces:
                    yield {"done": True, "text": "".join(pieces), "model": model_name,
                           "latency_ms": int((time.perf_counter() - started) * 1000),
                           "first_token_ms": first_token_ms,
                           "truncated": True, "error": last_error}
                    return
                if index + 1 < len(chain):
                    continue
                break

            if pieces:
                yield {"done": True, "text": "".join(pieces), "model": model_name,
                       "latency_ms": int((time.perf_counter() - started) * 1000),
                       "first_token_ms": first_token_ms}
                return
            last_error = last_error or "the model returned nothing"
            if index + 1 < len(chain):
                continue

        yield {"error": last_error or "Mistral request failed"}

    async def chat_json(self, messages: Sequence[Dict[str, str]], **kwargs: Any) -> Optional[dict]:
        result = await self.chat(messages, json_mode=True, **kwargs)
        if not result.ok:
            return None
        try:
            return json.loads(result.text)
        except json.JSONDecodeError:
            start, end = result.text.find("{"), result.text.rfind("}")
            if 0 <= start < end:
                try:
                    return json.loads(result.text[start:end + 1])
                except json.JSONDecodeError:
                    return None
            return None

    # ---------------------------------------------------------- embeddings
    async def embed(self, texts: Sequence[str], *,
                    urgent: bool = False) -> Optional[List[List[float]]]:
        """Return one vector per text, or None when embeddings are unavailable."""
        if not self.configured() or not texts:
            return None
        model = self.settings.mistral_embed_model
        wanted = list(texts)

        # Anything already embedded is free. A repeated question - the retry button,
        # the same query from two farmers - otherwise costs a full round trip before
        # retrieval can even start.
        out: List[Optional[List[float]]] = [None] * len(wanted)
        missing: List[int] = []
        for i, text in enumerate(wanted):
            key = _embed_key(model, text)
            hit = _embed_cache.get(key)
            if hit is not None:
                out[i] = hit
                _embed_cache.move_to_end(key)
            else:
                missing.append(i)
        if not missing:
            return [v for v in out if v is not None]

        payload = {"model": model, "input": [wanted[i] for i in missing]}
        try:
            data = await self._post(EMBED_URL, payload, urgent=urgent)
        except Exception as exc:                                     # noqa: BLE001
            log.warning("Mistral embeddings failed: %s", exc)
            return None
        try:
            ordered = sorted(data["data"], key=lambda d: d.get("index", 0))
            vectors = [item["embedding"] for item in ordered]
        except (KeyError, TypeError):
            return None
        if len(vectors) != len(missing):
            return None
        for slot, vector in zip(missing, vectors):
            out[slot] = vector
            _embed_cache[_embed_key(model, wanted[slot])] = vector
            while len(_embed_cache) > _EMBED_CACHE_MAX:
                _embed_cache.popitem(last=False)
        return [v for v in out if v is not None]


client = MistralClient()


# --------------------------------------------------------------- prompting
SYSTEM_PROMPT = (
    "You are Saarthi, an agricultural advisory assistant for Indian farmers.\n"
    "Hard rules:\n"
    "1. Use ONLY the agent findings and retrieved knowledge given to you. Never invent "
    "numbers, prices, pesticide doses, product names, registrations or label directions.\n"
    "2. Keep every number, unit and date exactly as supplied. Do not round, convert, add, "
    "divide or otherwise calculate with them - if a split or a per-acre figure is not in "
    "the findings, do not produce one.\n"
    "3. If a value is labelled simulated, estimated or unavailable, say so in the answer.\n"
    "4. If the information is insufficient, say what is missing and ask one short question.\n"
    "5. For pesticide, dose or high-risk decisions, tell the farmer to confirm with the "
    "product label or a local agriculture officer.\n"
    "6. The farm profile is already known. Never ask the farmer for something it "
    "already states, such as the crop or the field size.\n"
    "7. Add nothing of your own about profitability, yield, local practice or what is "
    "common in that area. If a claim is not in the findings, leave it out.\n"
    "8. Speak plainly, as to a farmer, in short sentences. Write continuous prose only: "
    "no markdown, no asterisks, no bold, no headings, no bullet points, no numbered "
    "lists. The answer may be read aloud over a phone call. 120 words maximum unless "
    "the farmer asked for detail.\n"
)

# The answer may be spoken by Azure TTS over a Twilio call, where "**" is read out
# or mangled. Models slip into markdown anyway, so strip it rather than hope.
_MARKDOWN_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*", re.DOTALL), r"\1"),       # bold
    (re.compile(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", re.DOTALL), r"\1"),  # italics
    (re.compile(r"(?m)^\s{0,3}#{1,6}\s*"), ""),             # headings
    (re.compile(r"(?m)^\s*[-*•]\s+"), ""),             # bullet markers
    (re.compile(r"(?m)^\s*\d+[.)]\s+"), ""),                # numbered list markers
    (re.compile(r"`{1,3}"), ""),                            # code ticks
    (re.compile(r"\n{3,}"), "\n\n"),
]


def clean_answer(text: str) -> str:
    cleaned = text or ""
    for pattern, replacement in _MARKDOWN_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)
    return cleaned.strip()


def language_directive(code: str) -> str:
    lang = languages.resolve(code)
    if lang.code == "en":
        # Without the explicit counter-examples the model mirrors the farmer's
        # Hinglish and ignores the language selected in the app. Naming the exact
        # failure mode is what makes a small model comply.
        return ("Reply in plain English only. The farmer selected English in the app, so "
                "answer in English even though the question may be typed in Hindi or in "
                "Hinglish. Never reply in Hinglish (Hindi words written in Latin "
                "letters): write 'water', not 'paani'; 'field', not 'khet'; 'fertiliser', "
                "not 'khad'; 'soil', not 'mitti'. Every word of your answer must be "
                "English.")
    # Smaller models drift into Roman transliteration ("khad ki dosa") when asked for
    # an Indian language, so name the script explicitly.
    script = {
        "hi": "Devanagari", "mr": "Devanagari", "bn": "Bengali", "ta": "Tamil",
        "te": "Telugu", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam",
        "pa": "Gurmukhi", "or": "Odia", "as": "Bengali", "ur": "Urdu (Nastaliq)",
    }.get(lang.code, lang.english_name)
    return (
        "Reply ONLY in %s (%s), written entirely in the %s script. Do NOT write Hindi or "
        "any other Indian language in Roman/Latin letters, and do not mix English "
        "sentences in. Technical words with no common local equivalent may stay in "
        "English. Numbers, units and dates stay exactly as given. Write every CROP NAME "
        "in English (wheat, rice, mustard, chickpea, maize), because translating a crop "
        "name is how the wrong crop gets advised - a model once rendered mustard as "
        "turmeric. You may add the local name in brackets after the English one if you "
        "are certain of it." % (lang.english_name, lang.native_name, script)
    )


def farm_block(farm: Any) -> str:
    """One line of farm context, so the model stops asking what it already knows."""
    if farm is None:
        return ""
    parts = [
        "village %s, %s, %s" % (farm.village, farm.district, farm.state),
        "plot %s (%s acres)" % (farm.parcel_id, farm.area_acres),
        "soil %s" % farm.soil_type,
        "irrigation %s" % farm.irrigation_method,
        "season %s" % farm.season,
    ]
    if farm.crop:
        parts.insert(0, "crop %s" % farm.crop)
    if farm.stage:
        parts.append("stage %s" % farm.stage)
    if farm.sowing_date:
        parts.append("sown %s" % farm.sowing_date)
    return "FARM PROFILE (known, do not ask again): " + ", ".join(parts) + "."


def support_block(support: Any) -> str:
    """Schemes, credit terms and helplines, as compact prompt text.

    Short lines rather than JSON: the model copies the portal and the number straight
    out of this, and every token spent on punctuation is one the answer does not get.
    """
    if not support:
        return ""
    lines = []
    for scheme in (support.get("schemes") or [])[:4]:
        lines.append("- %s: %s. Apply: %s" % (
            scheme.get("title", ""), scheme.get("benefit", ""),
            scheme.get("apply_at", "")))
    for line in (support.get("helplines") or [])[:3]:
        lines.append("- Helpline %s: %s" % (line.get("name", ""), line.get("number", "")))
    credit = support.get("credit") or {}
    if credit:
        lines.append("- Kisan Credit Card: %s%% a year if repaid on time, up to Rs %s "
                     "without security."
                     % (credit.get("kisan_credit_card_interest_percent"),
                        credit.get("collateral_free_limit_inr")))
    if not lines:
        return ""
    return ("SCHEMES, CREDIT AND HELPLINES (official; quote the portal or the number "
            "exactly as given, and never invent either):\n" + "\n".join(lines))


def build_answer_messages(question: str, language: str, agent_digest: str, knowledge: str,
                          history: Sequence[Dict[str, str]] = (),
                          farm: Any = None, support: Any = None) -> List[Dict[str, str]]:
    system = SYSTEM_PROMPT + "\n" + language_directive(language)
    messages: List[Dict[str, str]] = [{"role": "system", "content": system}]
    for turn in list(history)[-6:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": str(turn.get("content", ""))[:800]})
    context = []
    farm_text = farm_block(farm)
    if farm_text:
        context.append(farm_text)
    if agent_digest:
        context.append("AGENT FINDINGS (authoritative, already validated):\n" + agent_digest)
    if knowledge:
        context.append("RETRIEVED KNOWLEDGE (quote only what is relevant):\n" + knowledge)
    support_text = support_block(support)
    if support_text:
        context.append(support_text)
    context.append("FARMER QUESTION: " + question)
    # Repeat the language rule as the LAST line of the user turn. A system-prompt-only
    # directive is not enough for an 8B model: asked in Hinglish, it answers in
    # Hinglish and ignores the language the farmer selected in the app.
    context.append("ANSWER LANGUAGE (follow exactly): " + language_directive(language))
    messages.append({"role": "user", "content": "\n\n".join(context)})
    return messages
