import os
import sys
import asyncio
import httpx
import base64
import json
import logging
import re
from nacl import encoding, public
from datetime import datetime, timezone

import config

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
OWNER_DID = os.getenv("OWNER_DID")
BOT_DID = config.BOT_DID
PAT = os.getenv("PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
LAST_PROCESSED = os.getenv("LAST_PROCESSED", "").strip()

if not all([BOT_HANDLE, BOT_PASSWORD, OWNER_DID, PAT, GITHUB_REPOSITORY]):
    sys.exit(0)

def is_empty(value):
    if not value:
        return True
    v = value.strip().lower()
    return v in ("", "{}", "null", "none")

def encrypt_secret(pk, secret_value):
    pk = public.PublicKey(pk.encode("utf-8"), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(pk).encrypt(secret_value.encode("utf-8"))).decode("utf-8")

async def get_pubkey():
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key",
            headers={"Authorization": f"token {PAT}"}
        )
        return r.json()

async def update_last_processed_secret(value):
    try:
        kd = await get_pubkey()
        enc = encrypt_secret(kd["key"], value)
        async with httpx.AsyncClient() as c:
            r = await c.put(
                f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/LAST_PROCESSED",
                headers={"Authorization": f"token {PAT}"},
                json={"encrypted_value": enc, "key_id": kd["key_id"]}
            )
            if r.status_code in (201, 204):
                logger.info(f"Updated LAST_PROCESSED={value}")
                return True
    except Exception as e:
        logger.error(f"Failed to update secret: {e}")
    return False

async def fetch_with_retry(client, url, headers=None, params=None, max_retries=2, timeout=15):
    for attempt in range(max_retries):
        try:
            r = await client.get(url, headers=headers, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            if r.status_code >= 500 and attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            return r
        except httpx.RequestError as e:
            if attempt < max_retries - 1:
                await asyncio.sleep(1)
                continue
            raise

def normalize_uri(uri: str) -> str:
    if not uri:
        return ""
    if uri.startswith("at://"):
        return uri
    match = re.match(r"https://bsky\.app/profile/([^/]+)/post/([^/?#]+)", uri)
    if match:
        handle_or_did, rkey = match.groups()
        if handle_or_did.startswith("did:plc:"):
            return f"at://{handle_or_did}/app.bsky.feed.post/{rkey}"
    return uri

async def is_digest_post(client, token, uri: str) -> bool:
    if not uri:
        return False
    normalized = normalize_uri(uri)
    parts = normalized.split("/")
    if len(parts) < 5:
        return False
    did, collection, rkey = parts[2], parts[3], parts[4]
    try:
        r = await fetch_with_retry(
            client,
            f"https://bsky.social/xrpc/com.atproto.repo.getRecord",
            headers={"Authorization": f"Bearer {token}"},
            params={"repo": did, "collection": collection, "rkey": rkey},
            timeout=10
        )
        if r.status_code == 200:
            text = r.json().get("value", {}).get("text", "")
            return "Qwen | Chainbase TOPS" in text
    except Exception:
        pass
    return False

async def get_known_digest_uris(client, token) -> set:
    uris = set()
    for secret in ["LAST_DIGEST_URI", "ACTIVE_DIGEST_URI"]:
        try:
            r = await fetch_with_retry(
                client,
                f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret}",
                headers={"Authorization": f"token {PAT}"},
                timeout=10
            )
            if r.status_code == 200:
                val = r.json().get("value", "").strip()
                if val and val not in ("{}", "null", ""):
                    uris.add(normalize_uri(val))
        except Exception:
            pass
    return uris

async def main():
    try:
        if is_empty(LAST_PROCESSED):
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            logger.info(f"FIRST RUN: Setting timestamp to NOW: {now}")
            await update_last_processed_secret(now)
            sys.exit(0)
        
        logger.info(f"Checking notifications since {LAST_PROCESSED}")
        
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://bsky.social/xrpc/com.atproto.server.createSession",
                json={"identifier": BOT_HANDLE, "password": BOT_PASSWORD},
                timeout=10
            )
            if r.status_code != 200:
                raise Exception(f"Login failed: {r.status_code}")
            token = r.json()["accessJwt"]
            headers = {"Authorization": f"Bearer {token}"}
            
            known_digest_uris = await get_known_digest_uris(client, token)
            
            r = await fetch_with_retry(
                client,
                "https://bsky.social/xrpc/app.bsky.notification.listNotifications",
                headers=headers,
                params={"limit": 20},
                timeout=15
            )
            if r.status_code != 200:
                raise Exception(f"Fetch failed: {r.status_code}")
            notifications = r.json().get("notifications", [])
            relevant = []
            latest_idx = LAST_PROCESSED
            for n in notifications:
                idx = n.get("indexedAt", "")
                auth = n.get("author", {}).get("did", "")
                reason = n.get("reason", "")
                txt = (n.get("record", {}).get("text") or "").strip()
                uri = n.get("uri", "")
                if idx <= LAST_PROCESSED:
                    continue
                if idx > latest_idx:
                    latest_idx = idx
                has_t = "!t" in txt.lower()
                has_c = "!c" in txt.lower()
                has_trigger = has_t or has_c
                has_mention = f"@{BOT_HANDLE}" in txt
                if reason == "reply":
                    record = n.get("record", {})
                    reply_ref = record.get("reply", {})
                    parent_uri = reply_ref.get("parent", {}).get("uri") if reply_ref else None
                    normalized_parent = normalize_uri(parent_uri) if parent_uri else ""
                    is_deferred = False
                    if normalized_parent and normalized_parent in known_digest_uris:
                        is_deferred = True
                    elif parent_uri and await is_digest_post(client, token, parent_uri):
                        is_deferred = True
                    if is_deferred:
                        logger.info(f"[DEFER] Skipping digest reply (engagement will handle later): {txt[:50]}...")
                        continue
                if auth == OWNER_DID and reason == "reply":
                    search_type = "tavily" if has_t else ("chainbase" if has_c else None)
                    relevant.append({
                        "uri": uri,
                        "text": txt,
                        "has_search": has_trigger,
                        "search_type": search_type
                    })
                    logger.info(f"[QUEUE] Processing owner reply: {txt[:50]}... | search={has_trigger}")
                elif has_trigger or has_mention or reason == "reply":
                    search_type = "tavily" if has_t else ("chainbase" if has_c else None)
                    relevant.append({
                        "uri": uri,
                        "text": txt,
                        "has_search": has_trigger,
                        "search_type": search_type
                    })
                    logger.info(f"[QUEUE] Processing notification: {txt[:50]}... | search={has_trigger}")
            if relevant:
                github_output = os.getenv("GITHUB_OUTPUT", "")
                if github_output:
                    with open(github_output, "a") as f:
                        f.write("has_work=true\n")
                await update_last_processed_secret(latest_idx)
                with open("work_data.json", "w") as f:
                    json.dump({"items": relevant}, f)
                logger.info(f"Saved {len(relevant)} items for processing")
            else:
                if notifications:
                    await update_last_processed_secret(latest_idx)
                logger.info("No new relevant notifications.")
    except Exception as e:
        logger.error(f"Check failed: {e}")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
