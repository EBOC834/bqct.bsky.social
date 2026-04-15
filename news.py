import os
from datetime import datetime, timezone
import context as context_module
import search
import bsky

BOT_DID = os.getenv("BOT_DID")

def _should_post_news():
    now_utc = datetime.now(timezone.utc)
    last_ts_str = os.getenv("LAST_NEWS", "").strip()
    
    if not last_ts_str:
        print("[NEWS] LAST_NEWS empty. Allowing digest.")
        return True, now_utc.isoformat()
    
    try:
        last_ts_str = last_ts_str.replace("Z", "+00:00")
        last_ts = datetime.fromisoformat(last_ts_str)
        diff = now_utc - last_ts
        hours = diff.total_seconds() / 3600
        if hours >= 6:
            print(f"[NEWS] {hours:.1f}h passed. Allowing digest.")
            return True, now_utc.isoformat()
        print(f"[NEWS] Only {hours:.1f}h passed. Skipping digest.")
        return False, last_ts_str
    except Exception as e:
        print(f"[NEWS] Time parse error: {e}. Allowing digest.")
        return True, now_utc.isoformat()

async def post_news_if_due(client):
    should_post, new_ts = _should_post_news()
    if not should_post:
        return False
    
    trends = await search.chainbase_search("")
    if not trends or "No specific trends" in trends or "Error" in trends:
        print(f"[NEWS] Invalid trends. Skipping.")
        return False
    
    lines = [l.strip() for l in trends.split("\n") if l.strip().startswith("- ")][:3]
    if len(lines) < 3:
        print(f"[NEWS] Need 3 trends, got {len(lines)}. Skipping.")
        return False
    
    post_text = "Top 3 crypto trends:\n" + "\n".join(lines) + "\n\nQwen | Chainbase 💜💛"
    if len(post_text) > 300:
        post_text = post_text[:297] + "..."
    
    try:
        await bsky.post_root(client, BOT_DID, post_text)
        context_module.save_daily_post_ts(new_ts)
        print(f"[NEWS] Digest posted. LAST_NEWS updated to {new_ts}")
        return True
    except Exception as e:
        print(f"[NEWS] Post failed: {e}")
        return False
