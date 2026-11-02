import secrets
import threading
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field


router = APIRouter()

SESSION_DURATION = timedelta(hours=2)
OVERDUE_BLACKLIST_DURATION = timedelta(days=7)
MONTHLY_SESSION_LIMIT = 2
QUOTA_TIMEZONE = timezone(timedelta(hours=5, minutes=30))

SAMPLE_UNIT_ID = "sample-hardware-001"
SAMPLE_UNIT_CODE = "DEMO-GWALIOR-001"
SAMPLE_HUB_LATITUDE = 26.2183
SAMPLE_HUB_LONGITUDE = 78.1828


class UnitStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    IN_USE = "IN_USE"
    MAINTENANCE = "MAINTENANCE"


class QueueStatus(str, Enum):
    WAITING = "WAITING"
    ALLOCATED = "ALLOCATED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class SessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    OVERDUE = "OVERDUE"


@dataclass
class HardwareUnit:
    id: str
    unit_code: str
    hub_latitude: float
    hub_longitude: float
    status: UnitStatus = UnitStatus.AVAILABLE


@dataclass
class HardwareQueue:
    id: str
    hardware_id: str
    user_id: str
    queue_position: int
    status: QueueStatus
    created_at: datetime
    allocated_at: Optional[datetime] = None


@dataclass
class HardwareSession:
    id: str
    hardware_id: str
    user_id: str
    start_latitude: float
    start_longitude: float
    end_latitude: Optional[float]
    end_longitude: Optional[float]
    start_time: datetime
    expires_at: datetime
    returned_at: Optional[datetime]
    status: SessionStatus
    session_token: str


@dataclass
class UserBlacklist:
    id: str
    user_id: str
    reason: str
    blacklisted_until: datetime


class Coordinates(BaseModel):
    user_lat: Optional[float] = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    user_lng: Optional[float] = Field(default=None, ge=-180, le=180, allow_inf_nan=False)

    class Config:
        extra = "forbid"


class QueueJoinRequest(Coordinates):
    hardware_id: str = Field(min_length=1, max_length=100)


class QueueLeaveRequest(BaseModel):
    queue_id: str = Field(min_length=1, max_length=100)

    class Config:
        extra = "forbid"


class SessionStartRequest(BaseModel):
    hardware_id: str = Field(min_length=1, max_length=100)
    start_lat: Optional[float] = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    start_lng: Optional[float] = Field(default=None, ge=-180, le=180, allow_inf_nan=False)

    class Config:
        extra = "forbid"


class SessionReturnRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    session_token: str = Field(min_length=32, max_length=128)
    end_lat: Optional[float] = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    end_lng: Optional[float] = Field(default=None, ge=-180, le=180, allow_inf_nan=False)

    class Config:
        extra = "forbid"


_lock = threading.RLock()
_units: Dict[str, HardwareUnit] = {}
_queue: Dict[str, HardwareQueue] = {}
_sessions: Dict[str, HardwareSession] = {}
_blacklist: Dict[str, UserBlacklist] = {}
_penalized_sessions = set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seed_sample_unit() -> None:
    _units[SAMPLE_UNIT_ID] = HardwareUnit(
        id=SAMPLE_UNIT_ID,
        unit_code=SAMPLE_UNIT_CODE,
        hub_latitude=SAMPLE_HUB_LATITUDE,
        hub_longitude=SAMPLE_HUB_LONGITUDE,
    )


_seed_sample_unit()


def reset_state_for_tests() -> None:
    with _lock:
        _units.clear()
        _queue.clear()
        _sessions.clear()
        _blacklist.clear()
        _penalized_sessions.clear()
        _seed_sample_unit()


def _current_user(user_id: str) -> str:
    normalized = user_id.strip()
    if not normalized or len(normalized) > 128:
        raise HTTPException(status_code=422, detail="A valid X-User-Id header is required.")
    return normalized


def _get_unit(hardware_id: str) -> HardwareUnit:
    unit = _units.get(hardware_id)
    if unit is None:
        raise HTTPException(status_code=404, detail="Hardware unit was not found.")
    return unit


def _ensure_not_blacklisted(user_id: str, now: datetime) -> None:
    blacklist_entry = _blacklist.get(user_id)
    if blacklist_entry and blacklist_entry.blacklisted_until > now:
        raise HTTPException(
            status_code=403,
            detail={
                "message": "Your hardware-sharing access is temporarily suspended.",
                "blacklisted_until": blacklist_entry.blacklisted_until.isoformat(),
            },
        )


def _active_queue_for_unit(hardware_id: str) -> List[HardwareQueue]:
    return sorted(
        (
            entry
            for entry in _queue.values()
            if entry.hardware_id == hardware_id
            and entry.status in {QueueStatus.WAITING, QueueStatus.ALLOCATED}
        ),
        key=lambda entry: (entry.queue_position, entry.created_at, entry.id),
    )


def _reorder_queue(hardware_id: str) -> None:
    active_entries = _active_queue_for_unit(hardware_id)
    next_position = 1
    for entry in active_entries:
        entry.queue_position = next_position
        next_position += 1


def _promote_next_user(hardware_id: str, now: datetime) -> None:
    unit = _get_unit(hardware_id)
    if unit.status != UnitStatus.AVAILABLE:
        return
    active_entries = _active_queue_for_unit(hardware_id)
    for entry in active_entries:
        entry.status = QueueStatus.WAITING
    if active_entries:
        active_entries[0].status = QueueStatus.ALLOCATED
        active_entries[0].allocated_at = now
    _reorder_queue(hardware_id)


def _blacklist_user(user_id: str, session_id: str, now: datetime) -> None:
    if session_id in _penalized_sessions:
        return
    current = _blacklist.get(user_id)
    until = now + OVERDUE_BLACKLIST_DURATION
    if current and current.blacklisted_until > until:
        until = current.blacklisted_until
    _blacklist[user_id] = UserBlacklist(
        id=current.id if current else str(uuid.uuid4()),
        user_id=user_id,
        reason="Hardware session was overdue.",
        blacklisted_until=until,
    )
    _penalized_sessions.add(session_id)


def _monthly_session_count(user_id: str, now: datetime) -> int:
    month_start = now.astimezone(QUOTA_TIMEZONE).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    return sum(
        session.user_id == user_id
        and session.start_time.astimezone(QUOTA_TIMEZONE) >= month_start
        for session in _sessions.values()
    )


def process_overdue_sessions(now: Optional[datetime] = None) -> int:
    current_time = now or _utcnow()
    changed = 0
    with _lock:
        for session in _sessions.values():
            if (
                session.status == SessionStatus.ACTIVE
                and session.expires_at < current_time
            ):
                session.status = SessionStatus.OVERDUE
                _blacklist_user(session.user_id, session.id, current_time)
                changed += 1
    return changed


def _worker_loop(stop_event: threading.Event) -> None:
    while not stop_event.wait(60):
        process_overdue_sessions()


_worker_stop = threading.Event()
_worker_thread: Optional[threading.Thread] = None


def start_hardware_share_worker() -> None:
    global _worker_thread
    process_overdue_sessions()
    with _lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_stop.clear()
        _worker_thread = threading.Thread(
            target=_worker_loop,
            args=(_worker_stop,),
            name="hardware-share-overdue-worker",
            daemon=True,
        )
        _worker_thread.start()


def stop_hardware_share_worker() -> None:
    _worker_stop.set()
    if _worker_thread and _worker_thread.is_alive():
        _worker_thread.join(timeout=2)


def _queue_response(entry: HardwareQueue) -> dict:
    return {
        "id": entry.id,
        "hardware_id": entry.hardware_id,
        "user_id": entry.user_id,
        "queue_position": entry.queue_position,
        "status": entry.status.value,
        "created_at": entry.created_at.isoformat(),
        "allocated_at": entry.allocated_at.isoformat() if entry.allocated_at else None,
    }


@router.get("/api/hardware-share/units")
def list_hardware_units():
    with _lock:
        return {
            "units": [
                {
                    **asdict(unit),
                    "status": unit.status.value,
                    "is_sample": unit.id == SAMPLE_UNIT_ID,
                }
                for unit in _units.values()
            ],
            "storage": "in-memory; data resets when the process restarts",
        }


@router.post("/api/hardware-share/queue/join", status_code=status.HTTP_201_CREATED)
def join_hardware_queue(
    body: QueueJoinRequest,
    user_id: str = Header(..., alias="X-User-Id"),
):
    current_user = _current_user(user_id)
    now = _utcnow()
    with _lock:
        _ensure_not_blacklisted(current_user, now)
        unit = _get_unit(body.hardware_id)
        if unit.status == UnitStatus.MAINTENANCE:
            raise HTTPException(status_code=409, detail="This hardware unit is under maintenance.")
        if any(
            session.user_id == current_user
            and session.status in {SessionStatus.ACTIVE, SessionStatus.OVERDUE}
            for session in _sessions.values()
        ):
            raise HTTPException(status_code=409, detail="You already have an active hardware session.")
        if any(
            entry.user_id == current_user
            and entry.hardware_id == body.hardware_id
            and entry.status in {QueueStatus.WAITING, QueueStatus.ALLOCATED}
            for entry in _queue.values()
        ):
            raise HTTPException(status_code=409, detail="You are already in this hardware queue.")

        if _monthly_session_count(current_user, now) >= MONTHLY_SESSION_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="You have reached the limit of 2 hardware sessions this calendar month.",
            )

        active_entries = _active_queue_for_unit(body.hardware_id)
        entry = HardwareQueue(
            id=str(uuid.uuid4()),
            hardware_id=body.hardware_id,
            user_id=current_user,
            queue_position=len(active_entries) + 1,
            status=QueueStatus.WAITING,
            created_at=now,
        )
        _queue[entry.id] = entry
        if unit.status == UnitStatus.AVAILABLE and not active_entries:
            entry.status = QueueStatus.ALLOCATED
            entry.allocated_at = now
        return {
            "status": "success",
            "queue": _queue_response(entry),
        }


@router.post("/api/hardware-share/queue/leave")
def leave_hardware_queue(
    body: QueueLeaveRequest,
    user_id: str = Header(..., alias="X-User-Id"),
):
    current_user = _current_user(user_id)
    with _lock:
        entry = _queue.get(body.queue_id)
        if entry is None or entry.status not in {QueueStatus.WAITING, QueueStatus.ALLOCATED}:
            raise HTTPException(status_code=404, detail="Active queue entry was not found.")
        if entry.user_id != current_user:
            raise HTTPException(status_code=403, detail="You can only leave your own queue entry.")
        was_allocated = entry.status == QueueStatus.ALLOCATED
        entry.status = QueueStatus.CANCELLED
        unit = _get_unit(entry.hardware_id)
        if was_allocated and unit.status == UnitStatus.AVAILABLE:
            _promote_next_user(entry.hardware_id, _utcnow())
        else:
            _reorder_queue(entry.hardware_id)
        return {"status": "success", "queue": _queue_response(entry)}


@router.get("/api/hardware-share/queue/{queue_id}")
def get_hardware_queue_entry(
    queue_id: str,
    user_id: str = Header(..., alias="X-User-Id"),
):
    current_user = _current_user(user_id)
    with _lock:
        entry = _queue.get(queue_id)
        if entry is None or entry.status not in {QueueStatus.WAITING, QueueStatus.ALLOCATED}:
            raise HTTPException(status_code=404, detail="Active queue entry was not found.")
        if entry.user_id != current_user:
            raise HTTPException(status_code=403, detail="You can only view your own queue entry.")
        return {"status": "success", "queue": _queue_response(entry)}


@router.post("/api/hardware-share/session/start", status_code=status.HTTP_201_CREATED)
def start_hardware_session(
    body: SessionStartRequest,
    user_id: str = Header(..., alias="X-User-Id"),
):
    current_user = _current_user(user_id)
    now = _utcnow()
    with _lock:
        _ensure_not_blacklisted(current_user, now)
        unit = _get_unit(body.hardware_id)
        if _monthly_session_count(current_user, now) >= MONTHLY_SESSION_LIMIT:
            raise HTTPException(
                status_code=429,
                detail="You have reached the limit of 2 hardware sessions this calendar month.",
            )
        if any(
            session.user_id == current_user
            and session.status in {SessionStatus.ACTIVE, SessionStatus.OVERDUE}
            for session in _sessions.values()
        ):
            raise HTTPException(status_code=409, detail="You already have an active hardware session.")
        if unit.status != UnitStatus.AVAILABLE:
            raise HTTPException(status_code=409, detail="This hardware unit is not available.")
        allocated = next(
            (
                entry
                for entry in _active_queue_for_unit(body.hardware_id)
                if entry.user_id == current_user
                and entry.status == QueueStatus.ALLOCATED
                and entry.queue_position == 1
            ),
            None,
        )
        if allocated is None:
            raise HTTPException(
                status_code=409,
                detail="Only the user allocated at queue position 1 can start this session.",
            )
        expires_at = now + SESSION_DURATION
        session_record = HardwareSession(
            id=str(uuid.uuid4()),
            hardware_id=unit.id,
            user_id=current_user,
            start_latitude=body.start_lat,
            start_longitude=body.start_lng,
            end_latitude=None,
            end_longitude=None,
            start_time=now,
            expires_at=expires_at,
            returned_at=None,
            status=SessionStatus.ACTIVE,
            session_token=secrets.token_urlsafe(32),
        )
        _sessions[session_record.id] = session_record
        unit.status = UnitStatus.IN_USE
        del _queue[allocated.id]
        _reorder_queue(unit.id)
        return {
            "status": "success",
            "session": {
                "id": session_record.id,
                "hardware_id": session_record.hardware_id,
                "user_id": session_record.user_id,
                "start_time": now.isoformat(),
                "expires_at": expires_at.isoformat(),
                "status": session_record.status.value,
                "session_token": session_record.session_token,
            },
        }


@router.post("/api/hardware-share/session/return")
def return_hardware_session(
    body: SessionReturnRequest,
    user_id: str = Header(..., alias="X-User-Id"),
):
    current_user = _current_user(user_id)
    now = _utcnow()
    with _lock:
        session_record = _sessions.get(body.session_id)
        if session_record is None:
            raise HTTPException(status_code=404, detail="Hardware session was not found.")
        if session_record.user_id != current_user:
            raise HTTPException(status_code=403, detail="You can only return your own hardware session.")
        if not secrets.compare_digest(session_record.session_token, body.session_token):
            raise HTTPException(status_code=403, detail="The session token is invalid.")
        if session_record.returned_at is not None:
            raise HTTPException(status_code=409, detail="This hardware session has already been returned.")

        unit = _get_unit(session_record.hardware_id)
        session_record.end_latitude = body.end_lat
        session_record.end_longitude = body.end_lng
        session_record.returned_at = now
        if now > session_record.expires_at:
            session_record.status = SessionStatus.OVERDUE
            _blacklist_user(current_user, session_record.id, now)
        else:
            session_record.status = SessionStatus.COMPLETED
        unit.status = UnitStatus.AVAILABLE
        _promote_next_user(unit.id, now)
        return {
            "status": "success",
            "session": {
                "id": session_record.id,
                "status": session_record.status.value,
                "returned_at": now.isoformat(),
                "blacklisted_until": (
                    _blacklist[current_user].blacklisted_until.isoformat()
                    if session_record.status == SessionStatus.OVERDUE
                    else None
                ),
            },
            "next_queue": (
                _queue_response(_active_queue_for_unit(unit.id)[0])
                if _active_queue_for_unit(unit.id)
                else None
            ),
        }
