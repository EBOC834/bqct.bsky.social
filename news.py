import os
from datetime import datetime, timezone
import context as context_module
import search
import bsky

BOT_DID = os.getenv("BOT_DID")

def should_post():
    now_utc = datetime.now(timezone.utc)
    raw = os.getenv("LAST_NEWS", "").strip()
    if not raw or raw == "{}" or raw == "null":
        return True, now_utc.isoformat()
    try:
        last_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        diff = now_utc - last_ts
        if diff.total_seconds() >= 6 * 3600:
            return True, now_utc.isoformat()
        return False, raw
    except:
        return True, now_utc.isoformat()

async def post_if_due(client):
    should_post_flag, new_ts = should_post()
    if not should_post_flag:
        return False
    trends = await search.chainbase_search("")
    if not trends or "No specific trends" in trends or "Error" in trends:
        return False
    lines = [l.strip() for l in trends.split("\n") if l.strip().startswith("- ")][:3]
    if len(lines) < 1:
        return False
    post_text = "Top crypto trend:\n" + "\n".join(lines[:1]) + "\n\nQwen | Chainbase 💜💛"
    if len(post_text) > 300:
        post_text = post_text[:300]
    try:
        await bsky.post_root(client, BOT_DID, post_text)
        context_module.save_daily_post_ts(new_ts)
        return True
    except:
        return False
