import base64
import binascii
import math
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterator, Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import Response


router = APIRouter()
RADIUS_KM = 5
RETENTION_DAYS = 7
MAX_AUDIO_BYTES = 5 * 1024 * 1024
MAX_RESULTS = 50
INCIDENT_TYPES = {
    "Fire",
    "Flooding",
    "Animal intrusion",
    "Injury or medical emergency",
    "Other",
}
REACTIONS = {"seen", "can_help"}
ALLOWED_AUDIO_TYPES = {
    "audio/aac",
    "audio/mp4",
    "audio/mp3",
    "audio/mpeg",
    "audio/ogg",
    "audio/wav",
    "audio/wave",
    "audio/webm",
    "audio/x-m4a",
    "audio/x-wav",
}
DATABASE_PATH = Path(__file__).resolve().parents[2] / "sos_data" / "reports.sqlite3"
_cleanup_stop = threading.Event()
_cleanup_thread: Optional[threading.Thread] = None


def _connect() -> sqlite3.Connection:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS incidents (
            id TEXT PRIMARY KEY,
            incident_type TEXT NOT NULL,
            description TEXT NOT NULL,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            audio BLOB NOT NULL,
            audio_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reactions (
            incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
            device_id TEXT NOT NULL,
            reaction TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (incident_id, device_id)
        );
        CREATE INDEX IF NOT EXISTS incidents_expires_at ON incidents(expires_at);
        """
    )
    return connection


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def cleanup_expired_reports() -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _connection() as connection:
        connection.execute("DELETE FROM incidents WHERE expires_at <= ?", (now,))


def _cleanup_loop() -> None:
    while not _cleanup_stop.wait(60):
        try:
            cleanup_expired_reports()
        except sqlite3.Error as error:
            print(f"[WARN] SOS retention cleanup failed: {type(error).__name__}")


def start_sos_cleanup() -> None:
    global _cleanup_thread
    cleanup_expired_reports()
    if _cleanup_thread and _cleanup_thread.is_alive():
        return
    _cleanup_stop.clear()
    _cleanup_thread = threading.Thread(
        target=_cleanup_loop,
        name="sos-retention-cleanup",
        daemon=True,
    )
    _cleanup_thread.start()


def stop_sos_cleanup() -> None:
    _cleanup_stop.set()
    if _cleanup_thread and _cleanup_thread.is_alive():
        _cleanup_thread.join(timeout=2)


def _coordinate(value: object, name: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool):
        raise HTTPException(status_code=422, detail=f"{name} must be a valid coordinate.")
    try:
        coordinate = float(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail=f"{name} must be a valid coordinate.")
    if not math.isfinite(coordinate) or not minimum <= coordinate <= maximum:
        raise HTTPException(status_code=422, detail=f"{name} is outside the valid range.")
    return coordinate


def _distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat_delta = math.radians(lat2 - lat1)
    lon_delta = math.radians(lon2 - lon1)
    value = (
        math.sin(lat_delta / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(lon_delta / 2) ** 2
    )
    return 6371.0 * 2 * math.atan2(
        math.sqrt(max(0.0, value)),
        math.sqrt(max(0.0, 1 - value)),
    )


def _nearby_incident(
    connection: sqlite3.Connection, incident_id: str, latitude: float, longitude: float
) -> sqlite3.Row:
    incident = connection.execute(
        "SELECT id, latitude, longitude, expires_at FROM incidents WHERE id = ?",
        (incident_id,),
    ).fetchone()
    if not incident:
        raise HTTPException(status_code=404, detail="This SOS report is no longer available.")
    if incident["expires_at"] <= datetime.now(timezone.utc).isoformat():
        connection.execute("DELETE FROM incidents WHERE id = ?", (incident_id,))
        raise HTTPException(status_code=404, detail="This SOS report has expired.")
    distance = _distance_km(
        latitude, longitude, incident["latitude"], incident["longitude"]
    )
    if distance > RADIUS_KM:
        raise HTTPException(status_code=403, detail="This report is only available to nearby users.")
    return incident


@router.post("/api/sos", status_code=status.HTTP_201_CREATED)
def create_sos_report(body: Dict):
    incident_type = str(body.get("incident_type") or "").strip()
    description = str(body.get("description") or "").strip()
    if body.get("share_consent") is not True:
        raise HTTPException(
            status_code=422,
            detail="Confirm consent to share the voice note and exact incident location.",
        )
    if incident_type not in INCIDENT_TYPES:
        raise HTTPException(status_code=422, detail="Choose a valid incident type.")
    if not description or len(description) > 1000:
        raise HTTPException(status_code=422, detail="Incident details must be 1 to 1000 characters.")

    latitude = _coordinate(body.get("latitude"), "latitude", -90, 90)
    longitude = _coordinate(body.get("longitude"), "longitude", -180, 180)
    audio_type = str(body.get("audio_type") or "").split(";", 1)[0].strip().lower()
    if audio_type not in ALLOWED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="Upload a supported audio recording.")
    encoded_audio = body.get("audio_base64")
    if not isinstance(encoded_audio, str) or not encoded_audio:
        raise HTTPException(status_code=422, detail="Record or select a voice note before sending SOS.")
    if len(encoded_audio) > ((MAX_AUDIO_BYTES + 2) // 3) * 4:
        raise HTTPException(status_code=413, detail="Voice notes must be 5 MB or smaller.")
    try:
        audio = base64.b64decode(encoded_audio, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="The voice note upload is not valid base64 audio.")
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Voice notes must be between 1 byte and 5 MB.")

    now = datetime.now(timezone.utc)
    incident_id = str(uuid.uuid4())
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO incidents
                (id, incident_type, description, latitude, longitude, audio, audio_type, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                incident_type,
                description,
                latitude,
                longitude,
                sqlite3.Binary(audio),
                audio_type,
                now.isoformat(),
                (now + timedelta(days=RETENTION_DAYS)).isoformat(),
            ),
        )
    return {
        "status": "success",
        "incident_id": incident_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=RETENTION_DAYS)).isoformat(),
        "message": "SOS report saved to this local app instance.",
    }


@router.post("/api/sos/nearby")
def get_nearby_sos_reports(body: Dict):
    viewer_latitude = _coordinate(body.get("latitude"), "latitude", -90, 90)
    viewer_longitude = _coordinate(body.get("longitude"), "longitude", -180, 180)
    with _connection() as connection:
        connection.execute(
            "DELETE FROM incidents WHERE expires_at <= ?",
            (datetime.now(timezone.utc).isoformat(),),
        )
        incidents = connection.execute(
            """
            SELECT id, incident_type, description, latitude, longitude, audio_type, created_at, expires_at
            FROM incidents
            ORDER BY created_at DESC
            LIMIT 200
            """
        ).fetchall()
        visible = []
        for incident in incidents:
            distance = _distance_km(
                viewer_latitude,
                viewer_longitude,
                incident["latitude"],
                incident["longitude"],
            )
            if distance > RADIUS_KM:
                continue
            reactions = connection.execute(
                """
                SELECT reaction, COUNT(*) AS count
                FROM reactions WHERE incident_id = ?
                GROUP BY reaction
                """,
                (incident["id"],),
            ).fetchall()
            visible.append(
                {
                    "id": incident["id"],
                    "incident_type": incident["incident_type"],
                    "description": incident["description"],
                    "latitude": incident["latitude"],
                    "longitude": incident["longitude"],
                    "distance_km": round(distance, 2),
                    "audio_type": incident["audio_type"],
                    "audio_url": f"/api/sos/{incident['id']}/audio",
                    "created_at": incident["created_at"],
                    "expires_at": incident["expires_at"],
                    "reactions": {
                        row["reaction"]: row["count"] for row in reactions
                    },
                }
            )
            if len(visible) >= MAX_RESULTS:
                break
    return {"status": "success", "radius_km": RADIUS_KM, "reports": visible}


@router.post("/api/sos/{incident_id}/audio")
def play_sos_audio(incident_id: str, body: Dict):
    viewer_latitude = _coordinate(body.get("latitude"), "latitude", -90, 90)
    viewer_longitude = _coordinate(body.get("longitude"), "longitude", -180, 180)
    with _connection() as connection:
        _nearby_incident(connection, incident_id, viewer_latitude, viewer_longitude)
        incident = connection.execute(
            "SELECT audio, audio_type FROM incidents WHERE id = ?",
            (incident_id,),
        ).fetchone()
    if not incident:
        raise HTTPException(status_code=404, detail="This SOS report is no longer available.")
    return Response(
        content=incident["audio"],
        media_type=incident["audio_type"],
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/api/sos/{incident_id}/reaction")
def react_to_sos_report(incident_id: str, body: Dict):
    latitude = _coordinate(body.get("latitude"), "latitude", -90, 90)
    longitude = _coordinate(body.get("longitude"), "longitude", -180, 180)
    device_id = str(body.get("device_id") or "")
    reaction = str(body.get("reaction") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,80}", device_id):
        raise HTTPException(status_code=422, detail="A valid local device identifier is required.")
    if reaction not in REACTIONS:
        raise HTTPException(status_code=422, detail="Choose a valid reaction.")

    with _connection() as connection:
        _nearby_incident(connection, incident_id, latitude, longitude)
        connection.execute(
            """
            INSERT INTO reactions (incident_id, device_id, reaction, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(incident_id, device_id)
            DO UPDATE SET reaction = excluded.reaction, created_at = excluded.created_at
            """,
            (incident_id, device_id, reaction, datetime.now(timezone.utc).isoformat()),
        )
        counts = connection.execute(
            "SELECT reaction, COUNT(*) AS count FROM reactions WHERE incident_id = ? GROUP BY reaction",
            (incident_id,),
        ).fetchall()
    return {"status": "success", "reactions": {row["reaction"]: row["count"] for row in counts}}
