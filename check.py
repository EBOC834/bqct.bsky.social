#!/usr/bin/env python3
import os
import sys
import asyncio
import httpx
import base64
import json
from nacl import encoding, public
from datetime import datetime

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
OWNER_DID = os.getenv("OWNER_DID")
PAT = os.getenv("PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
LAST_PROCESSED = os.getenv("LAST_PROCESSED", "").strip()

if not all([BOT_HANDLE, BOT_PASSWORD, OWNER_DID, PAT, GITHUB_REPOSITORY]):
    print("❌ Missing env vars", file=sys.stderr)
    sys.exit(1)

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
                print(f"💾 Updated LAST_PROCESSED={value}", flush=True)
                return True
    except Exception as e:
        print(f"⚠️ Failed to update secret: {e}", flush=True)
    return False

async def main():
    try:
        # --- WARM START LOGIC ---
        if not LAST_PROCESSED:
            now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
            print(f"🔥 FIRST RUN: Secret is empty. Setting timestamp to NOW: {now}")
            print("No old notifications will be processed.")
            await update_last_processed_secret(now)
            print("✅ Initialized. Waiting for next run.", flush=True)
            sys.exit(0)
        
        print(f"🔍 Checking notifications since {LAST_PROCESSED}", flush=True)
        
        async with httpx.AsyncClient() as client:
            # Login
            r = await client.post(
                "https://bsky.social/xrpc/com.atproto.server.createSession",
                json={"identifier": BOT_HANDLE, "password": BOT_PASSWORD},
                timeout=30
            )
            if r.status_code != 200:
                raise Exception(f"Login failed: {r.status_code}")
            token = r.json()["accessJwt"]

            # Fetch Notifications
            r = await client.get(
                "https://bsky.social/xrpc/app.bsky.notification.listNotifications",
                headers={"Authorization": f"Bearer {token}"},
                params={"limit": 20},
                timeout=30
            )
            if r.status_code != 200:
                raise Exception(f"Fetch failed: {r.status_code}")
            
            notifications = r.json().get("notifications", [])
            print(f"📥 Found {len(notifications)} notifications", flush=True)

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

                # Filter: Only owner replies/mentions
                if auth != OWNER_DID:
                    continue
                if reason not in ("mention", "reply"):
                    continue
                
                # Check triggers or mention
                has_trigger = "!w" in txt.lower() or "!wa" in txt.lower()
                has_mention = f"@{BOT_HANDLE}" in txt
                
                # Process if trigger, mention, or just a reply from owner
                if has_trigger or has_mention or reason == "reply":
                    relevant.append({
                        "uri": uri,
                        "text": txt,
                        "has_search": has_trigger # Mark if it needs search
                    })
                    print(f"✅ Relevant: {txt[:30]}...", flush=True)

            # CRITICAL: Update LAST_PROCESSED immediately to lock this batch
            if relevant:
                await update_last_processed_secret(latest_idx)
                
                # Save work data for fresh_bot.py
                with open("work_data.json", "w") as f:
                    json.dump({"items": relevant}, f)
                
                print(f"🚀 Saved {len(relevant)} items to work_data.json", flush=True)
                sys.exit(0) # Success, there is work
            else:
                # Even if no relevant, update last processed to avoid re-checking old noise
                if notifications:
                     await update_last_processed_secret(latest_idx)
                print("ℹ️ No new relevant notifications.", flush=True)
                sys.exit(1) # No work

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
