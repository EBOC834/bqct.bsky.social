import os
import json
import time
import hashlib
import base64
import httpx
from nacl import encoding, public

PAT = os.getenv("PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
CONTEXT_SLOT_COUNT = int(os.getenv("CONTEXT_SLOT_COUNT", "10"))

def _encrypt(pk, value):
    pk_obj = public.PublicKey(pk.encode("utf-8"), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(pk_obj).encrypt(value.encode("utf-8"))).decode("utf-8")

def _headers():
    return {"Authorization": f"token {PAT}"}

def _read(name):
    for i in range(3):
        try:
            with httpx.Client() as c:
                r = c.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{name}", headers=_headers())
                if r.status_code == 200:
                    return r.json().get("value", "")
                if r.status_code in (429, 500, 502) and i < 2:
                    time.sleep(2 ** i)
        except:
            if i < 2: time.sleep(2 ** i)
    return ""

def _write(name, value):
    for i in range(3):
        try:
            with httpx.Client() as c:
                kr = c.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key", headers=_headers())
                kd = kr.json()
                enc = _encrypt(kd["key"], value)
                r = c.put(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{name}", headers=_headers(), json={"encrypted_value": enc, "key_id": kd["key_id"]})
                if r.status_code in (201, 204): return True
                if r.status_code in (429, 500, 502) and i < 2:
                    time.sleep(2 ** i)
        except:
            if i < 2: time.sleep(2 ** i)
    return False

def _slot(tid):
    return int(hashlib.sha256(tid.encode()).hexdigest(), 16) % CONTEXT_SLOT_COUNT

def load_context(tid):
    return _read(f"CONTEXT_{_slot(tid)}") or ""

def save_context(tid, summary):
    _write(f"CONTEXT_{_slot(tid)}", summary)

def load_last_digest_uri(): return _read("LAST_DIGEST_URI")
def save_last_digest_uri(uri): _write("LAST_DIGEST_URI", uri)
def load_active_digest_uri(): return _read("ACTIVE_DIGEST_URI")
def save_active_digest_uri(uri): _write("ACTIVE_DIGEST_URI", uri)
def save_daily_post_ts(ts): _write("LAST_NEWS", ts)

def merge_contexts(root_post, recent_posts, memory, search_results, user_question):
    parts = []
    if root_post: parts.append(f"@{root_post.get('handle', 'unknown')}: {root_post.get('text', '')}")
    for p in recent_posts: parts.append(f"@{p.get('handle', 'unknown')}: {p.get('text', '')}")
    if memory: parts.append(f"\n[Memory]:\n{memory}")
    if search_results: parts.append(f"\n[Search]:\n{search_results}")
    parts.append(f"\n[User]:\n{user_question}")
    return "\n".join(parts)
