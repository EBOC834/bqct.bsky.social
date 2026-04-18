import os
import json
import base64
import httpx
import hashlib
from nacl import encoding, public

PAT = os.getenv("PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
CONTEXT_SLOT_COUNT = int(os.getenv("CONTEXT_SLOT_COUNT", "10"))

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
                if r.status_code in (429, 500, 502) and i < 2:
                    import time
                    time.sleep(2 ** i)
        except:
            if i < 2:
                import time
                time.sleep(2 ** i)
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
                pk = key_data["key"]
                kid = key_data["key_id"]
                enc = encrypt_secret(pk, value)
                put_resp = client.put(
                    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}",
                    headers=_get_headers(),
                    json={"encrypted_value": enc, "key_id": kid}
                )
                if put_resp.status_code in (201, 204):
                    return True
                if put_resp.status_code in (429, 500, 502) and i < 2:
                    import time
                    time.sleep(2 ** i)
        except:
            if i < 2:
                import time
                time.sleep(2 ** i)
    return False

def _slot(tid):
    return int(hashlib.sha256(tid.encode()).hexdigest(), 16) % CONTEXT_SLOT_COUNT

def load_context(thread_id: str) -> str:
    return _read_secret(f"CONTEXT_{_slot(thread_id)}") or ""

def save_context(thread_id: str, summary: str):
    _write_secret(f"CONTEXT_{_slot(thread_id)}", summary)

def load_last_digest_uri() -> str:
    return _read_secret("LAST_DIGEST_URI") or ""

def save_last_digest_uri(uri: str):
    _write_secret("LAST_DIGEST_URI", uri)

def load_active_digest_uri() -> str:
    return _read_secret("ACTIVE_DIGEST_URI") or ""

def save_active_digest_uri(uri: str):
    _write_secret("ACTIVE_DIGEST_URI", uri)

def save_daily_post_ts(ts: str):
    _write_secret("LAST_NEWS", ts)

def merge_contexts(root_post, recent_posts, memory, search_results, user_question) -> str:
    parts = []
    if root_post:
        text = root_post.get("text", "")
        if root_post.get("link_hints"):
            text += "\n" + "\n".join(root_post["link_hints"])
        if root_post.get("alts"):
            text += "\n" + "\n".join(root_post["alts"])
        parts.append(f"@{root_post.get('handle', 'unknown')}: {text}")
    for p in recent_posts:
        text = p.get("text", "")
        if p.get("link_hints"):
            text += "\n" + "\n".join(p["link_hints"])
        if p.get("alts"):
            text += "\n" + "\n".join(p["alts"])
        parts.append(f"@{p.get('handle', 'unknown')}: {text}")
    if memory:
        parts.append(f"\n[Memory Summary]:\n{memory}")
    if search_results:
        parts.append(f"\n[Search Results]:\n{search_results}")
    parts.append(f"\n[User Question]:\n{user_question}")
    return "\n".join(parts)
