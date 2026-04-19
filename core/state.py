# core/state.py
import os
import json
import asyncio
import tempfile
import shutil
import logging
from core.config import STATE_FILE

logger = logging.getLogger(__name__)
_lock = asyncio.Lock()
_cache = {"contexts": {}, "timers": {}, "queue": [], "last_indexed": ""}

async def _load():
    global _cache
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                _cache.update(data)
        except Exception as e:
            logger.error(f"State load failed: {e}")

async def _save():
    global _cache
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(STATE_FILE))
    try:
        with os.fdopen(fd, 'w', encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, STATE_FILE)
    except Exception as e:
        logger.error(f"State save failed: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)

async def load_context(thread_id):
    async with _lock:
        return _cache["contexts"].get(thread_id, "")

async def save_context(thread_id, summary):
    async with _lock:
        _cache["contexts"][thread_id] = summary
        await _save()

async def load_timer(name):
    async with _lock:
        return _cache["timers"].get(name, "")

async def save_timer(name, value):
    async with _lock:
        _cache["timers"][name] = value
        await _save()

async def get_queue():
    async with _lock:
        return _cache.get("queue", [])

async def clear_queue():
    async with _lock:
        _cache["queue"] = []
        await _save()

async def set_last_indexed(idx):
    async with _lock:
        _cache["last_indexed"] = idx
        await _save()

async def load_last_indexed():
    async with _lock:
        return _cache.get("last_indexed", "")

async def init_state():
    await _load()
