import os
import json
import time
import httpx
import base64
import logging
from nacl import encoding, public

logger = logging.getLogger(__name__)
PAT = os.getenv("PAT")
REPO = os.getenv("GITHUB_REPOSITORY")
SLOT_COUNT = int(os.getenv("CONTEXT_SECRET_COUNT", "10"))

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
    
    max_retries = 3
    for attempt in range(max_retries):
        try:
            r = httpx.put(url, headers=headers, json=payload, timeout=15)
            r.raise_for_status()
            return
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait_time = (2 ** attempt) + 1
            logger.warning(f"Secrets API error, retrying in {wait_time}s: {e}")
            time.sleep(wait_time)

def _read_slot(i):
    raw = os.getenv(f"CONTEXT_{i}", "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None

def load_context(thread_id):
    for i in range(SLOT_COUNT):
        data = _read_slot(i)
        if data and data.get("thread_id") == thread_id:
            return data.get("content", "")
    return ""

def save_context(thread_id, content):
    slots = [(i, _read_slot(i)) for i in range(SLOT_COUNT)]
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
        logger.info(f"Saved context to CONTEXT_{target_idx}")
    except Exception as e:
        logger.error(f"Failed to save context: {e}")

def load_last_digest_uri():
    return os.getenv("LAST_DIGEST_URI", "").strip() or None

def save_last_digest_uri(uri):
    try:
        _write_secret("LAST_DIGEST_URI", uri)
        logger.info(f"Saved LAST_DIGEST_URI: {uri}")
    except Exception as e:
        logger.error(f"Failed to save LAST_DIGEST_URI: {e}")

def merge_contexts(root_post: dict, recent_posts: list, memory: str, search_results: str, user_question: str = "") -> str:
    parts = []
    if root_post and root_post.get("text"):
        marker = " [ROOT]" if root_post.get("is_root") else ""
        line = f"@{root_post.get('handle', 'unknown')}{marker}: {root_post['text']}"
        if root_post.get("embed"):
            line += f" {root_post['embed']}"
        if root_post.get("link_hints"):
            line += " " + " ".join(root_post["link_hints"])
        parts.append(line)
    if user_question:
        parts.append(f"\n[User Question]: {user_question}")
    for p in recent_posts:
        if p.get("is_root"):
            continue
        marker = " [BOT]" if p.get("handle") == os.getenv("BOT_HANDLE") else ""
        line = f"@{p.get('handle', 'unknown')}{marker}: {p.get('text', '')}"
        if p.get("embed"):
            line += f" {p['embed']}"
        if p.get("link_hints"):
            line += " " + " ".join(p["link_hints"])
        parts.append(line)
    if memory:
        parts.append(f"\n[Thread Summary]:\n{memory}")
    if search_results:
        parts.append(f"\n[Search Results]:\n{search_results}")
    all_alts = []
    for p in [root_post] + recent_posts:
        if p and p.get("alts"):
            all_alts.extend(p["alts"])
    if all_alts:
        parts.append(f"\n[Image/Video alts: {'; '.join(set(all_alts))}]")
    return "\n".join(parts)

def load_daily_post_ts():
    raw = os.getenv("LAST_NEWS", "").strip()
    return raw if raw else None

def save_daily_post_ts(ts_str):
    try:
        _write_secret("LAST_NEWS", ts_str)
        logger.info("Saved LAST_NEWS timestamp")
    except Exception as e:
        logger.error(f"Failed to save LAST_NEWS: {e}")
