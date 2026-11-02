"""Small async HTTP helper with a TTL cache and an explicit failure shape.

Every external data service in Saarthi returns a ServiceResult rather than
raising, because an advisory with a missing forecast is still useful - it just
has to say the forecast is missing instead of inventing one.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

import httpx

log = logging.getLogger("saarthi.services")

_cache: Dict[str, Tuple[float, Any]] = {}
_lock = asyncio.Lock()

# Test hook: fn(url, params) -> dict | None. When set, no network call is made.
Fetcher = Callable[[str, Dict[str, Any]], Any]
_fetcher: Optional[Fetcher] = None


def set_fetcher(fetcher: Optional[Fetcher]) -> None:
    global _fetcher
    _fetcher = fetcher


def clear_cache() -> None:
    _cache.clear()


@dataclass
class _Failure:
    """Marker stored in the cache so a known-bad lookup is not retried at once."""

    error: str


@dataclass
class ServiceResult:
    ok: bool
    data: Any = None
    source: str = ""
    url: str = ""
    fetched_at: float = field(default_factory=time.time)
    error: str = ""
    cached: bool = False

    @property
    def age_seconds(self) -> int:
        return int(time.time() - self.fetched_at)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "error": self.error,
            "age_seconds": self.age_seconds,
            "cached": self.cached,
        }


async def _remember_failure(key: str, error: str, source: str, url: str) -> ServiceResult:
    async with _lock:
        _cache[key] = (time.time(), _Failure(error=error))
    return ServiceResult(ok=False, source=source, url=url, error=error)


async def get_json(url: str, params: Optional[Dict[str, Any]] = None, *, source: str = "",
                   ttl: int = 600, timeout: float = 20.0, failure_ttl: int = 300,
                   headers: Optional[Dict[str, str]] = None) -> ServiceResult:
    """Fetch JSON with a TTL cache.

    Failures are cached too, for ``failure_ttl``. Without that, a service that is
    merely slow (SoilGrids can take 20 s and then time out) is re-tried on every
    single request and silently becomes the latency of the whole application.
    """
    params = params or {}
    key = url + "?" + "&".join("%s=%s" % (k, params[k]) for k in sorted(params))
    now = time.time()
    async with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            if isinstance(hit[1], _Failure):
                if now - hit[0] < failure_ttl:
                    return ServiceResult(ok=False, source=source, url=url, fetched_at=hit[0],
                                         cached=True, error=hit[1].error)
            else:
                return ServiceResult(ok=True, data=hit[1], source=source, url=url,
                                     fetched_at=hit[0], cached=True)
    if _fetcher is not None:
        try:
            data = _fetcher(url, params)
            if asyncio.iscoroutine(data):
                data = await data
        except Exception as exc:                                  # noqa: BLE001
            return ServiceResult(ok=False, source=source, url=url, error=str(exc)[:200])
        if data is None:
            return ServiceResult(ok=False, source=source, url=url,
                                 error="test fetcher returned no data")
        async with _lock:
            _cache[key] = (now, data)
        return ServiceResult(ok=True, data=data, source=source, url=url, fetched_at=now)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, params=params,
                                    headers=headers or {"User-Agent": "Saarthi/2.0 (agri advisory)"})
        if resp.status_code != 200:
            return await _remember_failure(key, "HTTP %s" % resp.status_code, source, url)
        data = resp.json()
    except Exception as exc:                                      # noqa: BLE001
        # httpx timeouts stringify to "", so the reason has to be built from the type
        # when the message is empty - otherwise both the log line and the page end up
        # saying "unavailable:" and nothing else.
        reason = str(exc).strip() or type(exc).__name__
        if "Timeout" in type(exc).__name__:
            reason = "no answer within %.0f s (%s)" % (timeout, type(exc).__name__)
        log.info("%s unavailable: %s", source or url, reason)
        return await _remember_failure(key, reason[:200], source, url)
    async with _lock:
        _cache[key] = (now, data)
    return ServiceResult(ok=True, data=data, source=source, url=url, fetched_at=now)
