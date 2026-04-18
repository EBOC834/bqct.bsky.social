import os
import json
import time
import hashlib
import base64
import httpx
from nacl import encoding, public
from config import PAT, GITHUB_REPOSITORY, CONTEXT_SLOT_COUNT

def encrypt_secret(pk, secret_value):
    pk_obj = public.PublicKey(pk.encode("utf-8"), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(pk_obj).encrypt(secret_value.encode("utf-8"))).decode("utf-8")

def _get_headers():
    return {"Authorization": f"token {PAT}"}

def _read_secret(secret_name: str) -> str:
    for i in range(3):
        try:
            with httpx.Client() as client:
                r = client.get(
                    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}",
                    headers=_get_headers()
                )
                if r.status_code == 200:
                    return r.json().get("value", "")
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(2 ** i)
                    continue
                return ""
        except Exception:
            if i < 2: time.sleep(2 ** i)
    return ""

def _write_secret(secret_name: str, value: str):
    for i in range(3):
        try:
            with httpx.Client() as client:
                key_resp = client.get(
                    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key",
                    headers=_get_headers()
                )
                key_data = key_resp.json()
                enc = encrypt_secret(key_data["key"], value)
                put_resp = client.put(
                    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}",
                    headers=_get_headers(),
                    json={"encrypted_value": enc, "key_id": key_data["key_id"]}
                )
                if put_resp.status_code in (201, 204):
                    return True
                if put_resp.status_code in (429, 500, 502, 503):
                    time.sleep(2 ** i)
                    continue
                return False
        except Exception:
            if i < 2: time.sleep(2 ** i)
    return False

def load_context(thread_id: str) -> str:
    slot = int(hashlib.sha256(thread_id.encode()).hexdigest(), 16) % CONTEXT_SLOT_COUNT
    return _read_secret(f"CONTEXT_{slot}")

def save_context(thread_id: str, summary: str):
    slot = int(hashlib.sha256(thread_id.encode()).hexdigest(), 16) % CONTEXT_SLOT_COUNT
    _write_secret(f"CONTEXT_{slot}", summary)

def load_last_digest_uri() -> str:
    return _read_secret("LAST_DIGEST_URI")

def save_last_digest_uri(uri: str):
    _write_secret("LAST_DIGEST_URI", uri)

def load_active_digest_uri() -> str:
    return _read_secret("ACTIVE_DIGEST_URI")

def save_active_digest_uri(uri: str):
    _write_secret("ACTIVE_DIGEST_URI", uri)

def save_daily_post_ts(ts: str):
    _write_secret("LAST_NEWS", ts)

def merge_contexts(root_post, recent_posts, memory, search_results, user_question) -> str:
    parts = []
    if root_post:
        parts.append(f"@{root_post.get('handle', 'unknown')}: {root_post.get('text', '')}")
    for p in recent_posts:
        parts.append(f"@{p.get('handle', 'unknown')}: {p.get('text', '')}")
    if memory:
        parts.append(f"\n[Memory Summary]:\n{memory}")
    if search_results:
        parts.append(f"\n[Search Results]:\n{search_results}")
    parts.append(f"\n[User Question]:\n{user_question}")
    return "\n".join(parts)
