import os
import json
import httpx

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
OWNER_DID = os.getenv("OWNER_DID")
LAST_PROCESSED = os.getenv("LAST_PROCESSED", "")

async def main():
    print(f"Checking notifications since {LAST_PROCESSED or 'beginning'}")
    
    async with httpx.AsyncClient() as client:
        login = await client.post(
            "https://bsky.social/xrpc/com.atproto.server.createSession",
            json={"identifier": BOT_HANDLE, "password": BOT_PASSWORD}
        )
        if login.status_code != 200:
            return
        token = login.json().get("accessJwt")
        if not token:
            return
        
        url = "https://bsky.social/xrpc/app.bsky.notification.listNotifications?limit=50"
        if LAST_PROCESSED:
            url += f"&seenAt={LAST_PROCESSED}"
        headers = {"Authorization": f"Bearer {token}"}
        r = await client.get(url, headers=headers, timeout=30)
        if r.status_code != 200:
            return
        notifs = r.json().get("notifications", [])

    if not notifs:
        print("No new notifications.")
        return

    relevant = []
    latest_ts = LAST_PROCESSED
    for n in notifs:
        if n.get("author", {}).get("did") != OWNER_DID:
            continue
        record = n.get("record", {})
        uri = record.get("uri") if isinstance(record, dict) else None
        text = record.get("text", "") if isinstance(record, dict) else ""
        if not uri or not text:
            continue
        relevant.append({"uri": uri, "text": text})
        ts = n.get("indexedAt", "")
        if ts and (not latest_ts or ts > latest_ts):
            latest_ts = ts

    if not relevant:
        print("No relevant notifications.")
        return

    with open("work_data.json", "w") as f:
        json.dump({"items": relevant}, f)
    if latest_ts:
        print(f"Updated LAST_PROCESSED={latest_ts}")
    print(f"Found {len(relevant)} notification(s).")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
