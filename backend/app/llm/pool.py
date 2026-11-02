"""One HTTP connection pool for every model call.

Each Mistral request used to open its own ``httpx.AsyncClient``, which means a
fresh TCP connection and a fresh TLS handshake to api.mistral.ai every single
time. Measured from this machine against ministral-8b-latest, same prompt:

    cold client per request   1009 ms
    pooled, connection reused  744 ms

265 ms of every answer was handshake. The pool keeps the connection open
between turns, so only the first call of the process pays for it.

The client is created lazily inside the running loop - building it at import
time binds it to whichever loop happened to be current, and the second loop
(tests, or a worker thread) then gets "Event loop is closed".
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

import httpx

log = logging.getLogger("saarthi.llm")

# Long enough that an idle minute between turns still finds the connection open.
KEEPALIVE_SECONDS = 300.0

_clients: Dict[int, httpx.AsyncClient] = {}
_lock = asyncio.Lock()


def _loop_key() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:                                   # no loop: never pooled
        return 0


async def client(timeout: float = 45.0) -> httpx.AsyncClient:
    """The pooled client for the current event loop."""
    key = _loop_key()
    existing = _clients.get(key)
    if existing is not None and not existing.is_closed:
        return existing
    async with _lock:
        existing = _clients.get(key)
        if existing is not None and not existing.is_closed:
            return existing
        created = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16,
                                keepalive_expiry=KEEPALIVE_SECONDS),
            follow_redirects=False,
        )
        _clients[key] = created
        return created


async def close() -> None:
    """Close the pool for this loop. Called on shutdown and between tests."""
    key = _loop_key()
    existing = _clients.pop(key, None)
    if existing is not None and not existing.is_closed:
        await existing.aclose()


async def warm(url: str, headers: Optional[Dict[str, str]] = None) -> bool:
    """Open the TLS connection before anyone is waiting on it.

    Called at startup. A HEAD that 404s is a perfectly good outcome here: the
    point is the handshake, not the response.
    """
    try:
        pooled = await client()
        await pooled.head(url, headers=headers or {}, timeout=8.0)
        return True
    except httpx.HTTPError as exc:
        log.info("Connection warm-up for %s skipped: %s", url, str(exc)[:80])
        return False
