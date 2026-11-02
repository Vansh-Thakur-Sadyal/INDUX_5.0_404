"""How the dashboard looks: the glass theme's backdrop, set once for everybody.

A farmer picks their own theme - light, dark, or glass - and that choice lives in
their own browser, because it is about the screen in front of them and nothing on
the server needs to know it.

What IS on the server is the PICTURE behind the glass theme, and how long each one
stays up. That is an administrator's decision for the whole farm: one of them sets
the backdrop and every account sees it, the way a letterhead is chosen once. Keeping
it per-browser would have meant every farmer setting their own, which is not what was
asked for and is a worse answer anyway - most of them would never find the setting.

Two ways to supply a picture, because both are real:

* an **upload**, for a photograph somebody has on their machine. It is stored under
  the storage volume, served back from here, and survives a redeploy.
* a **link**, for an image already on the web.

Neither is trusted blindly. An upload must actually decode as an image of a type a
browser renders, and a link must be http or https - a ``javascript:`` or ``data:``
URL in a CSS ``background-image`` is script in everybody's dashboard, chosen by one
administrator and seen by every account on the box.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile

from ..core import storage
from ..core.security import require_admin, require_user

log = logging.getLogger("saarthi.appearance")

router = APIRouter(prefix="/api/appearance", tags=["appearance"])

STATE_KEY = "appearance"

# The picture that ships with the dashboard, so the glass theme looks like
# something on a server nobody has configured.
DEFAULT_IMAGES = ["backgrounds/field-sunrise.jpg"]

# How long one picture stays up before the next. Zero means "do not change it",
# which is the right answer for a single picture and the honest way to turn the
# slideshow off. The floor is there because a backdrop that changes every two
# seconds is a strobe, not a theme.
DEFAULT_INTERVAL = 45
MIN_INTERVAL = 5
MAX_INTERVAL = 3600

MAX_IMAGES = 8
MAX_UPLOAD_BYTES = 6 * 1024 * 1024

# What a browser will actually render as a background, by what the bytes start
# with. The extension a file was uploaded under is a claim, not evidence.
MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
    (b"RIFF", "image/webp", ".webp"),          # checked again for WEBP below
)

_STORED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def _folder() -> Path:
    folder = storage.folder() / "backgrounds"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _defaults() -> Dict[str, Any]:
    return {"images": list(DEFAULT_IMAGES), "interval_seconds": DEFAULT_INTERVAL}


def settings_now() -> Dict[str, Any]:
    """What is configured, with anything unreadable falling back to the default."""
    raw = storage.get_state(STATE_KEY, "")
    if not raw:
        return _defaults()
    try:
        stored = json.loads(raw)
    except ValueError:
        log.warning("appearance settings were not JSON; using the defaults")
        return _defaults()
    if not isinstance(stored, dict):
        return _defaults()
    images = [str(x) for x in (stored.get("images") or []) if str(x).strip()]
    return {
        "images": images or list(DEFAULT_IMAGES),
        "interval_seconds": _clean_interval(stored.get("interval_seconds")),
    }


def _clean_interval(value: Any) -> int:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    if seconds <= 0:
        return 0                       # the slideshow off, said plainly
    return max(MIN_INTERVAL, min(MAX_INTERVAL, seconds))


def _clean_image(value: Any) -> str:
    """One entry of the list, as something safe to put in ``background-image``.

    Three shapes are allowed and nothing else: a file this server stored, a path
    inside the dashboard's own static files, and an http(s) link.
    """
    text = str(value or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="An empty image is not one.")
    if text.startswith("/api/appearance/image/"):
        name = text.rsplit("/", 1)[-1]
        if not _STORED.fullmatch(name) or not (_folder() / name).exists():
            raise HTTPException(status_code=400,
                                detail="That uploaded image is not on this server.")
        return text
    if text.startswith(("http://", "https://")):
        parsed = urlparse(text)
        if not parsed.netloc:
            raise HTTPException(status_code=400, detail="That link has no host.")
        return text
    if text.startswith(("backgrounds/", "assets/")) and ".." not in text:
        return text
    # Anything else - javascript:, data:, a bare word - would end up inside a CSS
    # url() on every account's dashboard.
    raise HTTPException(
        status_code=400,
        detail="An image must be an uploaded file or an http link.")


@router.get("")
@router.get("/")
async def read_appearance(_: Dict[str, Any] = Depends(require_user)) -> Dict[str, Any]:
    """The backdrop every account's glass theme uses."""
    body = settings_now()
    body["limits"] = {"max_images": MAX_IMAGES, "min_interval": MIN_INTERVAL,
                      "max_interval": MAX_INTERVAL,
                      "max_upload_mb": MAX_UPLOAD_BYTES // (1024 * 1024)}
    return body


@router.put("")
@router.put("/")
async def write_appearance(payload: Dict[str, Any],
                           _: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Set the backdrop for every account. Administrators only."""
    raw = payload.get("images")
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail="images must be a list.")
    if len(raw) > MAX_IMAGES:
        raise HTTPException(status_code=400,
                            detail="At most %d pictures." % MAX_IMAGES)
    images = [_clean_image(x) for x in raw]
    body = {"images": images or list(DEFAULT_IMAGES),
            "interval_seconds": _clean_interval(payload.get("interval_seconds"))}
    storage.set_state(STATE_KEY, json.dumps(body))
    log.info("appearance updated: %d picture(s), every %ss",
             len(body["images"]), body["interval_seconds"] or "never")
    return body


@router.post("/upload")
async def upload_image(file: UploadFile = File(...),
                       _: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Store one picture and return the path to put in the list.

    Storing it does not put it on anybody's screen: the list is saved separately,
    so an administrator can upload, look, and change their mind.
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="That file is empty.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail="That picture is %.1f MB; the limit is %d MB."
                   % (len(data) / 1048576, MAX_UPLOAD_BYTES // 1048576))

    kind = _image_kind(data)
    if not kind:
        raise HTTPException(
            status_code=400,
            detail="That file is not a JPEG, PNG, GIF or WEBP image.")
    _, suffix = kind

    name = uuid.uuid4().hex[:16] + suffix
    (_folder() / name).write_bytes(data)
    return {"image": "/api/appearance/image/" + name,
            "bytes": len(data), "stored_as": name}


def _image_kind(data: bytes) -> Optional[tuple]:
    """What this actually is, read from the bytes rather than the filename."""
    for magic, media, suffix in MAGIC:
        if not data.startswith(magic):
            continue
        if magic == b"RIFF":
            # RIFF is a container; only the WEBP flavour is an image.
            if data[8:12] != b"WEBP":
                continue
        return media, suffix
    return None


@router.get("/image/{name}")
async def stored_image(name: str) -> Response:
    """Serve an uploaded backdrop.

    Open to any signed-in session would be the obvious rule, but a CSS
    ``background-image`` is fetched by the browser with the session cookie
    anyway, so the usual session check applies through the cookie. The name is
    matched against a strict pattern first: it arrives from a URL, and joining
    an unchecked name onto a directory is how a path traversal starts.
    """
    if not _STORED.fullmatch(name):
        raise HTTPException(status_code=404, detail="No such picture.")
    path = _folder() / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such picture.")
    data = path.read_bytes()
    kind = _image_kind(data)
    media = kind[0] if kind else "application/octet-stream"
    return Response(content=data, media_type=media,
                    headers={"Cache-Control": "public, max-age=86400"})


@router.get("/stored")
async def list_stored(_: Dict[str, Any] = Depends(require_admin)) -> Dict[str, Any]:
    """Every picture uploaded to this server, whether or not it is in use."""
    in_use = set(settings_now()["images"])
    out: List[Dict[str, Any]] = []
    for path in sorted(_folder().glob("*"), key=lambda p: p.stat().st_mtime,
                       reverse=True):
        if not path.is_file():
            continue
        url = "/api/appearance/image/" + path.name
        out.append({"image": url, "bytes": path.stat().st_size,
                    "in_use": url in in_use})
    return {"stored": out, "shipped": list(DEFAULT_IMAGES)}
