import os
from datetime import datetime, timezone
import context as context_module
import search
import bsky

BOT_DID = os.getenv("BOT_DID")

def _should_post_news():
    now_utc = datetime.now(timezone.utc)
    raw = os.getenv("LAST_NEWS", "").strip()
    print(f"[NEWS] LAST_NEWS value: '{raw}'")
    if not raw or raw == "{}" or raw == "null":
        print("[NEWS] Empty or invalid. Allowing digest.")
        return True, now_utc.isoformat()
    try:
        last_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        diff = now_utc - last_ts
        hours = diff.total_seconds() / 3600
        if hours >= 6:
            print(f"[NEWS] {hours:.1f}h passed. Allowing digest.")
            return True, now_utc.isoformat()
        print(f"[NEWS] {hours:.1f}h passed. Skipping.")
        return False, raw
    except Exception as e:
        print(f"[NEWS] Parse error: {e}. Allowing digest.")
        return True, now_utc.isoformat()

async def post_news_if_due(client):
    should_post, new_ts = _should_post_news()
    if not should_post:
        return False
    print("[NEWS] Fetching Chainbase trends...")
    trends = await search.chainbase_search("")
    print(f"[NEWS] Trends response length: {len(trends) if trends else 0}")
    if not trends or "No specific trends" in trends or "Error" in trends:
        print("[NEWS] Invalid trends data. Skipping.")
        return False
    lines = [l.strip() for l in trends.split("\n") if l.strip().startswith("- ")][:3]
    print(f"[NEWS] Parsed {len(lines)} trend lines.")
    if len(lines) < 3:
        print(f"[NEWS] Less than 3 trends. Skipping. Raw: {trends[:200]}")
        return False
    post_text = "Top 3 crypto trends:\n" + "\n".join(lines) + "\n\nQwen | Chainbase 💜💛"
    if len(post_text) > 300:
        post_text = post_text[:297] + "..."
    try:
        print("[NEWS] Posting to Bluesky...")
        await bsky.post_root(client, BOT_DID, post_text)
        context_module.save_daily_post_ts(new_ts)
        print(f"[NEWS] Posted successfully. LAST_NEWS set to {new_ts}")
        return True
    except Exception as e:
        print(f"[NEWS] Post failed: {e}")
        return False
