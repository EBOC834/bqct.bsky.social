import os
import time
import hashlib
import base64
import httpx
import logging
from nacl import encoding, public

logger = logging.getLogger(__name__)

PAT = os.getenv("PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
CONTEXT_SLOT_COUNT = int(os.getenv("CONTEXT_SLOT_COUNT", "30"))

def encrypt_secret(pk, value):
    pk_obj = public.PublicKey(pk.encode("utf-8"), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(pk_obj).encrypt(value.encode("utf-8"))).decode("utf-8")

def _headers():
    return {"Authorization": f"token {PAT}"}

def _read_secret(name):
    for i in range(3):
        try:
            with httpx.Client() as c:
                r = c.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{name}", headers=_headers())
                if r.status_code == 200:
                    val = r.json().get("value", "")
                    logger.info(f"Secret read: {name} ({len(val) if val else 0} chars)")
                    return val
                if r.status_code == 404:
                    logger.info(f"Secret not found (first use): {name}")
                    return ""
                if r.status_code >= 400 and i < 2:
                    time.sleep(2 ** i)
        except Exception as e:
            logger.warning(f"Read secret {name} attempt {i+1} failed: {e}")
            if i < 2: time.sleep(2 ** i)
    return ""

def _write_secret(name, value):
    for i in range(3):
        try:
            with httpx.Client() as c:
                kr = c.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key", headers=_headers())
                if kr.status_code != 200:
                    if i < 2: time.sleep(2 ** i)
                    continue
                kd = kr.json()
                enc = encrypt_secret(kd["key"], value)
                r = c.put(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{name}", headers=_headers(), json={"encrypted_value": enc, "key_id": kd["key_id"]})
                if r.status_code in (201, 204):
                    logger.info(f"Secret written: {name}")
                    return True
                if r.status_code >= 400 and i < 2:
                    logger.warning(f"Write secret {name} attempt {i+1} failed: {r.status_code}")
                    time.sleep(2 ** i)
        except Exception as e:
            logger.warning(f"Write secret {name} attempt {i+1} failed: {e}")
            if i < 2: time.sleep(2 ** i)
    logger.error(f"Failed to write secret after 3 attempts: {name}")
    return False

def _slot(tid):
    return int(hashlib.sha256(tid.encode()).hexdigest(), 16) % CONTEXT_SLOT_COUNT

def load_context(tid):
    return _read_secret(f"CONTEXT_{_slot(tid)}")

def save_context(tid, summary):
    _write_secret(f"CONTEXT_{_slot(tid)}", summary)

def load_last_digest_uri():
    return _read_secret("LAST_DIGEST_URI")

def save_last_digest_uri(uri):
    _write_secret("LAST_DIGEST_URI", uri)

def load_active_digest_uri():
    return _read_secret("ACTIVE_DIGEST_URI")

def save_active_digest_uri(uri):
    _write_secret("ACTIVE_DIGEST_URI", uri)

def save_daily_post_ts(ts):
    _write_secret("LAST_NEWS", ts)

def build_prompt(thread_data, memory, search_res, user_text):
    parts = []
    if thread_data.get("root_text"):
        parts.append(f"[ROOT] @{thread_data['root_author']}: {thread_data['root_text']}")
    if memory:
        parts.append(f"[Memory]: {memory}")
    if search_res:
        parts.append(f"[Search]: {search_res}")
    parts.append(f"[User]: {user_text}")
    return "\n".join(parts)
