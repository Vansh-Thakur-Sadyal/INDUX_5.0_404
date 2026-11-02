# ==========================================
# SHARED PIPELINE PROGRESS STATE
# Written by the orchestrator/agents, read by
# the frontend through GET /progress.
# ==========================================

import threading

_lock = threading.Lock()

_state = {
    "running": False,
    "step": 0,      # 1..5 → which agent is currently working
    "total": 5,
    "label": "idle",
}


def start():
    with _lock:
        _state.update(running=True, step=0, label="starting")


def set_step(step, label):
    with _lock:
        _state.update(running=True, step=step, label=label)


def finish(success=True):
    with _lock:
        if success:
            _state.update(running=False, step=_state["total"], label="complete")
        else:
            _state.update(running=False, label="error")


def get():
    with _lock:
        return dict(_state)
