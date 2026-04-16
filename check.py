import os
import re
import json
import httpx
from datetime import datetime, timezone

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
OWNER_DID = os.getenv("OWNER_DID")
LAST_PROCESSED = os.getenv("LAST_PROCESSED", "")

async def login(client, handle, password):
    await client.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password}
    )

async def fetch_notifications(client, since):
    url = "https://bsky.social/xrpc/app.bsky.notification.listNotifications?limit=50"
    if since:
        url += f"&seenAt={since}"
    r = await client.get(url, timeout=30)
    if r.status_code == 200:
        return r.json().get("notifications", [])
    return []

def parse_operators(text):
    t = text.lower()
    if "!c" in t or "!chainbase" in t:
        return True, "chainbase"
    if "!t" in t or "!tavily" in t:
        return True, "tavily"
    return False, "tavily"

async def main():
    print(f"Checking notifications since {LAST_PROCESSED or 'beginning'}")
    
    async with httpx.AsyncClient() as client:
        await login(client, BOT_HANDLE, BOT_PASSWORD)
        notifs = await fetch_notifications(client, LAST_PROCESSED)
    
    if not notifs:
        print("No new notifications.")
        return

    relevant = []
    latest_ts = LAST_PROCESSED

    for n in notifs:
        author_did = n.get("author", {}).get("did", "")
        if author_did != OWNER_DID:
            continue
        
        record = n.get("record", {})
        uri = record.get("uri") if isinstance(record, dict) else None
        if not uri:
            continue
        
        text = record.get("text", "") if isinstance(record, dict) else ""
        if not text:
            continue

        has_search, stype = parse_operators(text)
        clean = re.sub(r'![tc]\b', '', text, flags=re.IGNORECASE).strip()
        clean = re.sub(r'\s+', ' ', clean).strip()

        relevant.append({"uri": uri, "text": clean, "has_search": has_search, "search_type": stype})
        
        ts = n.get("indexedAt", "")
        if ts and (not latest_ts or ts > latest_ts):
            latest_ts = ts

    if not relevant:
        print("No new relevant notifications.")
        return

    with open("work_data.json", "w") as f:
        json.dump({"items": relevant}, f)

    if latest_ts:
        print(f"Updated LAST_PROCESSED={latest_ts}")

    print(f"Found {len(relevant)} relevant notification(s).")
    for i in relevant:
        print(f"  - {i['text'][:80]}... [search={i['has_search']}, type={i['search_type']}]")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
