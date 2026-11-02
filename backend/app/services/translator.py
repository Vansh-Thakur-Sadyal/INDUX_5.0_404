"""Azure Translator: the machine that turns the interface into fourteen languages.

Why this exists beside the language model
-----------------------------------------
Interface translation used to be a Mistral prompt. It worked, and it was the
wrong tool three ways over:

  * **Rate limits.** The free tier answers 429 under load, and a rate-limited
    chunk fell through to the English fallback - which is how a farmer who
    chose Bengali got half a dashboard in Bengali and half in English, with
    nothing on screen saying why.
  * **Latency.** A cold screen is six hundred labels. At forty per model call,
    with retries, that was tens of seconds. Azure takes eighty strings in one
    request and answers in under half a second.
  * **Shape.** A model asked for JSON sometimes returns prose, so the reply had
    to be parsed defensively and anything unparseable was lost. Azure returns
    one translation per input, in order, every time.

The model stays as the fallback: if Azure is not configured, or refuses, the
caller drops back to it rather than showing English.

What is deliberately not translated
-----------------------------------
Scheme names (PM-KISAN, KCC, PMFBY), units and the product name are sent inside
Azure's dynamic-dictionary markup, which tells the engine to pass them through
unchanged. "PM-KISAN" translated into Bengali is not a scheme a farmer can
search for.

The Wikipedia tail
------------------
Azure's Assamese and Nepali models were trained on Wikipedia and sometimes
append the article furniture with it: "Soil Health" comes back as
"<Assamese>[edit]" - the word, then the edit link. Measured on this account on
2026-11-02. A trailing bracketed run that the source does not have is stripped,
because a button labelled "[edit]" is worse than no translation at all.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, List, Optional, Sequence

import httpx

from ..core.config import get_settings

log = logging.getLogger("saarthi.translator")

# Azure accepts 1000 elements and 50,000 characters per request. Neither limit is
# worth approaching: a smaller batch fails smaller, and the round trip is the cost,
# not the payload.
MAX_ITEMS = 80
MAX_CHARS = 20000
TIMEOUT = 20.0
ATTEMPTS = 3

# Terms that must survive translation exactly. A scheme a farmer cannot type into
# a search box is not a scheme they can apply for.
KEEP = (
    "PM-KISAN", "PMFBY", "KCC", "PMKSY", "eNAM", "e-NAM", "MSP", "Agmarknet",
    "Saarthi", "VisionCoders", "SoilQ", "Kisan Mitra", "NPK", "pH", "dS/m",
    # The slot a caller's own name is put into. It has to survive
    # translation EXACTLY or the name cannot be swapped back in: Azure
    # transliterates a name it recognises, so a Latin placeholder comes
    # back in Gurmukhi and the code looking for it finds nothing.
    "XNAMEX",
)
_KEEP_RE = {term: re.compile(r"(?<![A-Za-z0-9-])%s(?![A-Za-z0-9-])" % re.escape(term))
            for term in KEEP}

# "...[edit]" in whatever script. Only stripped when the English had no bracket of
# its own, so a label that really is "Yield (kg)" keeps its own parenthesis.
_TAIL = re.compile(r"\s*[\[(][^\[\]()]{1,40}[\])]\s*$")
_HAS_BRACKET = re.compile(r"[\[(]")

_DICT_OPEN = '<mstrans:dictionary translation="%s">%s</mstrans:dictionary>'


def _settings():
    return get_settings()


def configured() -> bool:
    return bool(_settings().get("AZURE_TRANSLATOR_KEY"))


def region() -> str:
    return _settings().get("AZURE_TRANSLATOR_REGION", "centralindia")


def endpoint() -> str:
    return _settings().get("AZURE_TRANSLATOR_ENDPOINT",
                           "https://api.cognitive.microsofttranslator.com").rstrip("/")


def status() -> Dict[str, object]:
    return {
        "configured": configured(),
        "region": region(),
        "endpoint": endpoint(),
        "note": "" if configured() else
                ("AZURE_TRANSLATOR_KEY is not set; interface translation falls "
                 "back to the language model."),
    }


def _protect(text: str) -> str:
    """Wrap the keep-as-is terms in Azure's dynamic-dictionary markup."""
    out = text
    low = text.lower()
    for term, pattern in _KEEP_RE.items():
        if term.lower() in low:
            out = pattern.sub(_DICT_OPEN % (term, term), out)
    return out


def _clean(source: str, translated: str) -> str:
    """Undo the one thing the engine adds that we never asked for."""
    body = (translated or "").strip()
    if not body:
        return source
    if not _HAS_BRACKET.search(source):
        for _ in range(2):
            stripped = _TAIL.sub("", body).strip()
            if stripped == body or not stripped:
                break
            body = stripped
    return body or source


async def _post(client: httpx.AsyncClient, target: str,
                chunk: Sequence[str]) -> Optional[List[str]]:
    """One request. None means it could not be translated at all."""
    params = {"api-version": "3.0", "from": "en", "to": target,
              "textType": "plain", "profanityAction": "NoAction"}
    headers = {
        "Ocp-Apim-Subscription-Key": _settings().get("AZURE_TRANSLATOR_KEY"),
        "Ocp-Apim-Subscription-Region": region(),
        "Content-Type": "application/json",
    }
    body = [{"Text": _protect(t)} for t in chunk]

    for attempt in range(ATTEMPTS):
        try:
            response = await client.post(endpoint() + "/translate", params=params,
                                         headers=headers, json=body)
        except Exception as exc:                                   # noqa: BLE001
            log.info("translator unreachable (%s): %s", target, str(exc)[:160])
            if attempt == ATTEMPTS - 1:
                return None
            await asyncio.sleep(0.6 * (attempt + 1))
            continue

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                return None
            # One entry per input, in order. Anything else and the mapping cannot
            # be trusted, so nothing is returned rather than risk mapping labels
            # onto the wrong controls.
            if not isinstance(data, list) or len(data) != len(chunk):
                log.info("translator returned the wrong number of entries for %s",
                         target)
                return None
            out: List[str] = []
            for source, item in zip(chunk, data):
                picks = (item or {}).get("translations") or []
                out.append(_clean(source, picks[0].get("text") if picks else ""))
            return out

        # 429 is a throttle with a Retry-After; 5xx is worth one more go. Anything
        # else - a bad key, an unsupported language - will not improve on a retry.
        if response.status_code in (429, 500, 502, 503, 504):
            wait = float(response.headers.get("Retry-After") or 0) or 0.8 * (attempt + 1)
            log.info("translator HTTP %s for %s; waiting %.1fs",
                     response.status_code, target, wait)
            await asyncio.sleep(min(wait, 5.0))
            continue

        log.warning("translator refused (%s): HTTP %s %s", target,
                    response.status_code, response.text[:200])
        return None
    return None


def _chunks(texts: Sequence[str]) -> List[List[str]]:
    out: List[List[str]] = []
    current: List[str] = []
    size = 0
    for text in texts:
        length = len(text) + 64            # the dictionary markup is not free
        if current and (len(current) >= MAX_ITEMS or size + length > MAX_CHARS):
            out.append(current)
            current, size = [], 0
        current.append(text)
        size += length
    if current:
        out.append(current)
    return out


async def translate(target: str, texts: Sequence[str]) -> Dict[str, str]:
    """English to one language. Returns what was HANDLED; never raises.

    A string the engine returned unchanged is in the result, mapped to itself.
    That is not padding: it is the difference between "Azure could not do this"
    and "Azure was told to leave this alone and did". Product and scheme names
    go through the dynamic dictionary and come back identical, and dropping them
    here meant the caller thought they had been missed and sent them to the
    language model, which duly translated "Saarthi 2.0" into "सार्थी २.०".

    Only a string that is genuinely absent is left out, and that one the caller
    should show in English - which is its decision to make, so nothing is
    invented here.
    """
    wanted = [t for t in dict.fromkeys(texts) if t and t.strip()]
    if not wanted or not configured() or target in ("", "en"):
        return {}

    pairs: Dict[str, str] = {}
    batches = _chunks(wanted)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        # Four at a time. The whole point of this service is that a cold screen
        # takes a second or two, and a cold screen is a dozen chunks.
        for start in range(0, len(batches), 4):
            group = batches[start:start + 4]
            results = await asyncio.gather(
                *[_post(client, target, chunk) for chunk in group])
            for chunk, result in zip(group, results):
                if not result:
                    continue
                for source, translated in zip(chunk, result):
                    if translated:
                        pairs[source] = translated
    return pairs
