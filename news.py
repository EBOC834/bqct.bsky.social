import os
from datetime import datetime, timezone
import context as context_module
import search
import bsky

BOT_DID = os.getenv("BOT_DID")

def should_post_news():
    now_utc = datetime.now(timezone.utc)
    last_ts_str = os.getenv("LAST_NEWS", "").strip()
    if not last_ts_str:
        return True
    try:
        last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
        diff = now_utc - last_ts
        return diff.total_seconds() >= 6 * 3600
    except:
        return True

async def post_news_if_due(client):
    trends = await search.chainbase_search("")
    if not trends or "No specific trends" in trends or "Error" in trends:
        return False
    lines = [l.strip() for l in trends.split("\n") if l.strip().startswith("- ")][:3]
    if len(lines) < 3:
        return False
    post_text = "Top 3 crypto trends:\n" + "\n".join(lines) + "\n\nQwen | Chainbase 💜💛"
    if len(post_text) > 300:
        post_text = post_text[:297] + "..."
    try:
        await bsky.post_root(client, BOT_DID, post_text)
        now_utc = datetime.now(timezone.utc).isoformat()
        context_module.save_daily_post_ts(now_utc)
        return True
    except:
        return False
