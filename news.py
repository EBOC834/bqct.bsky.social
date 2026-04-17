import os
import logging
from datetime import datetime, timezone
import state
import search
import bsky
import generator
import engagement

logger = logging.getLogger(__name__)
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
    except Exception:
        return True, now_utc.isoformat()

async def post_if_due(client, llm):
    should_post_flag, new_ts = should_post()
    if not should_post_flag:
        return False

    last_digest_uri = state.load_last_digest_uri()
    if last_digest_uri and llm:
        try:
            last_rec = await bsky.get_record(client, last_digest_uri)
            last_digest_text = last_rec["value"].get("text", "") if last_rec else ""
            await engagement.process_digest_engagement(client, llm, last_digest_uri, last_digest_text)
        except Exception as e:
            logger.error(f"Failed to process previous digest engagement: {e}")

    trends = await search.chainbase_search("")
    if not trends or "No specific trends" in trends or "Error" in trends:
        return False

    lines = [l.strip() for l in trends.split("\n") if l.strip().startswith("- ")]
    if not lines:
        return False

    final_line = generator.generate_digest(llm, lines[0])
    post_text = final_line + "\n\nQwen | Chainbase TOPS 💜💛"

    if len(post_text) > 300:
        post_text = post_text[:300].rsplit(' ', 1)[0] + "\n\nQwen | Chainbase TOPS 💜💛"

    try:
        resp = await bsky.post_root(client, BOT_DID, post_text)
        new_uri = resp.get("uri")
        if new_uri:
            state.save_last_digest_uri(new_uri)
        state.save_daily_post_ts(new_ts)
        return True
    except Exception as e:
        logger.error(f"[NEWS] Post failed: {e}")
        return False
