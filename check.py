import os
import sys
import asyncio
import httpx
import base64
import json
import logging
from nacl import encoding, public
from datetime import datetime, timezone

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
OWNER_DID = os.getenv("OWNER_DID")
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
        r = await c.get(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key", headers={"Authorization": f"token {PAT}"})
        return r.json()

async def update_last_processed_secret(value):
    try:
        kd = await get_pubkey()
        enc = encrypt_secret(kd["key"], value)
        async with httpx.AsyncClient() as c:
            r = await c.put(f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/LAST_PROCESSED", headers={"Authorization": f"token {PAT}"}, json={"encrypted_value": enc, "key_id": kd["key_id"]})
            if r.status_code in (201, 204):
                logger.info(f"Updated LAST_PROCESSED={value}")
                return True
    except Exception as e:
        logger.error(f"Failed to update secret: {e}")
        return False

async def main():
    try:
        if is_empty(LAST_PROCESSED):
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            logger.info(f"FIRST RUN: Setting timestamp to NOW: {now}")
            await update_last_processed_secret(now)
            sys.exit(0)
        logger.info(f"Checking notifications since {LAST_PROCESSED}")
        async with httpx.AsyncClient() as client:
            r = await client.post("https://bsky.social/xrpc/com.atproto.server.createSession", json={"identifier": BOT_HANDLE, "password": BOT_PASSWORD}, timeout=30)
            if r.status_code != 200:
                raise Exception(f"Login failed: {r.status_code}")
            token = r.json()["accessJwt"]
            r = await client.get("https://bsky.social/xrpc/app.bsky.notification.listNotifications", headers={"Authorization": f"Bearer {token}"}, params={"limit": 20}, timeout=30)
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
                if auth != OWNER_DID:
                    continue
                if reason not in ("mention", "reply"):
                    continue
                has_t = "!t" in txt.lower()
                has_c = "!c" in txt.lower()
                has_trigger = has_t or has_c
                has_mention = f"@{BOT_HANDLE}" in txt
                if has_trigger or has_mention or reason == "reply":
                    search_type = "tavily" if has_t else ("chainbase" if has_c else None)
                    relevant.append({"uri": uri, "text": txt, "has_search": has_trigger, "search_type": search_type})
                    logger.info(f"Relevant notification found: {txt[:30]}...")
            if relevant:
                github_output = os.getenv("GITHUB_OUTPUT", "")
                if github_output:
                    with open(github_output, "a") as f:
                        f.write("has_work=true\n")
                await update_last_processed_secret(latest_idx)
                with open("work_data.json", "w") as f:
                    json.dump({"items": relevant}, f)
                logger.info(f"Saved {len(relevant)} items to work_data.json")
            else:
                if notifications:
                    await update_last_processed_secret(latest_idx)
                logger.info("No new relevant notifications.")
    except Exception as e:
        logger.error(f"Check failed: {e}")
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
