import os
from datetime import datetime, timezone, timedelta

import context
import search
import bsky

BOT_DID = os.getenv("BOT_DID")

def _get_est_date():
    now_utc = datetime.now(timezone.utc)
    return now_utc.astimezone(timezone(timedelta(hours=-5)))

def _is_post_due():
    now_est = _get_est_date()
    if now_est.hour < 18:
        return False
    today = now_est.strftime("%Y-%m-%d")
    last = context.load_daily_post_date()
    if last and last.get("date") == today:
        return False
    return True, today

async def post_daily_digest(client):
    due_result = _is_post_due()
    if not due_result:
        return False
    is_due, today_date = due_result
    trends = await search.chainbase_search("")
    if not trends or "No specific trends" in trends or "Error" in trends:
        return False
    lines = [l.strip() for l in trends.split("\n") if l.strip().startswith("- ")][:3]
    if len(lines) < 3:
        return False
    post_text = "Top 3 crypto trends today:\n" + "\n".join(lines) + "\n\nQwen | Chainbase 💜💛"
    if len(post_text) > 300:
        post_text = post_text[:297] + "..."
    try:
        await bsky.post_root(client, BOT_DID, post_text)
        context.save_daily_post_date(today_date)
        return True
    except Exception:
        return False
