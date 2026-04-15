import os
import json
import time
import httpx
import base64
from nacl import encoding, public

PAT = os.getenv("PAT")
REPO = os.getenv("GITHUB_REPOSITORY")
SECRET_COUNT = int(os.getenv("CONTEXT_SECRET_COUNT", "10"))

def _get_public_key():
    url = f"https://api.github.com/repos/{REPO}/actions/secrets/public-key"
    headers = {"Authorization": f"token {PAT}", "Accept": "application/vnd.github.v3+json"}
    r = httpx.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def _encrypt_secret(public_key_str, value):
    public_key = public.PublicKey(public_key_str.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")

def _write_secret(name, value):
    key_data = _get_public_key()
    encrypted_value = _encrypt_secret(key_data["key"], value)
    url = f"https://api.github.com/repos/{REPO}/actions/secrets/{name}"
    headers = {"Authorization": f"token {PAT}", "Accept": "application/vnd.github.v3+json"}
    payload = {"encrypted_value": encrypted_value, "key_id": key_data["key_id"]}
    r = httpx.put(url, headers=headers, json=payload, timeout=15)
    r.raise_for_status()

def _parse_slot(i):
    raw = os.getenv(f"CONTEXT_{i}", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except:
        return None

def load_context(thread_id):
    for i in range(SECRET_COUNT):
        data = _parse_slot(i)
        if data and data.get("thread_id") == thread_id:
            return data.get("content", "")
    return ""

def save_context(thread_id, content):
    slots = [(i, _parse_slot(i)) for i in range(SECRET_COUNT)]
    target_idx = None
    for i, data in slots:
        if data and data.get("thread_id") == thread_id:
            target_idx = i
            break
    if target_idx is None:
        oldest_idx, oldest_ts = 0, float("inf")
        for i, data in slots:
            if data is None:
                oldest_idx = i
                break
            ts = data.get("ts", 0)
            if ts < oldest_ts:
                oldest_ts = ts
                oldest_idx = i
        target_idx = oldest_idx
    payload = json.dumps({"thread_id": thread_id, "content": content, "ts": int(time.time())}, ensure_ascii=False)
    try:
        _write_secret(f"CONTEXT_{target_idx}", payload)
    except:
        pass

def load_daily_post_date():
    raw = os.getenv("DAILY_POST_DATE", "")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if not data:
            return None
        return data
    except:
        return None

def save_daily_post_date(date_str):
    payload = json.dumps({"date": date_str, "ts": int(time.time())}, ensure_ascii=False)
    try:
        _write_secret("DAILY_POST_DATE", payload)
    except:
        pass
