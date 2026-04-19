import os
import json
from hashlib import sha256
from core.config import STATE_FILE, CONTEXT_SLOT_COUNT

def _read_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"contexts": {}, "timers": {}, "queue": [], "last_indexed": ""}

def _write_state(data):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_context(thread_id):
    slot = int(sha256(thread_id.encode()).hexdigest(), 16) % CONTEXT_SLOT_COUNT
    state = _read_state()
    return state["contexts"].get(f"context_{slot}", "")

def save_context(thread_id, summary):
    slot = int(sha256(thread_id.encode()).hexdigest(), 16) % CONTEXT_SLOT_COUNT
    state = _read_state()
    state["contexts"][f"context_{slot}"] = summary
    _write_state(state)

def load_timer(name):
    return _read_state()["timers"].get(name, "")

def save_timer(name, value):
    state = _read_state()
    state["timers"][name] = value
    _write_state(state)

def get_queue():
    return _read_state().get("queue", [])

def clear_queue():
    state = _read_state()
    state["queue"] = []
    _write_state(state)

def set_last_indexed(idx):
    state = _read_state()
    state["last_indexed"] = idx
    _write_state(state)

def load_last_indexed():
    return _read_state().get("last_indexed", "")
