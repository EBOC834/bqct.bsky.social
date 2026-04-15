import os
from datetime import datetime, timezone

import context as context_module
import search
import bsky

BOT_DID = os.getenv("BOT_DID")

def _should_post_news():
    now_utc = datetime.now(timezone.utc)
    last_ts_str = os.getenv("LAST_NEWS", "")
    
    if not last_ts_str:
        return True, now_utc.isoformat()
    
    try:
        last_ts = datetime.fromisoformat(last_ts_str.replace("Z", "+00:00"))
        diff = now_utc - last_ts
        if diff.total_seconds() >= 6 * 3600:
            return True, now_utc.isoformat()
        return False, last_ts_str
    except:
        return True, now_utc.isoformat()

async def post_news_if_due(client):
    should_post, new_ts = _should_post_news()
    if not should_post:
        print(f"[NEWS] Skipping: last post was {new_ts}", flush=True)
        return False
    
    trends = await search.chainbase_search("")
    if not trends or "No specific trends" in trends or "Error" in trends:
        print(f"[NEWS] No valid trends", flush=True)
        return False
    
    lines = [l.strip() for l in trends.split("\n") if l.strip().startswith("- ")][:3]
    if len(lines) < 3:
        print(f"[NEWS] Less than 3 trends", flush=True)
        return False
    
    post_text = "Top 3 crypto trends:\n" + "\n".join(lines) + "\n\nQwen | Chainbase 💜💛"
    if len(post_text) > 300:
        post_text = post_text[:297] + "..."
    
    try:
        await bsky.post_root(client, BOT_DID, post_text)
        context_module.save_daily_post_ts(new_ts)
        print(f"[NEWS] Posted at {new_ts}", flush=True)
        return True
    except Exception as e:
        print(f"[NEWS] Failed: {e}", flush=True)
        return False
