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

def get_trend_emoji(rank_status: str) -> str:
    status = (rank_status or "same").lower()
    if status == "new":
        return "🆕"
    elif status == "up":
        return "↗️"
    elif status == "down":
        return "↘️"
    return "➡️"

def should_post():
    now_utc = datetime.now(timezone.utc)
    raw = os.getenv("LAST_NEWS", "").strip()
    if not raw or raw == "{}" or raw == "null":
        return True, now_utc.isoformat(), "full"
    try:
        last_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        diff = now_utc - last_ts
        seconds = diff.total_seconds()
        if seconds >= 6 * 3600:
            return True, now_utc.isoformat(), "full"
        elif seconds >= 3 * 3600:
            return True, now_utc.isoformat(), "mini"
        return False, raw, None
    except Exception:
        return True, now_utc.isoformat(), "full"

async def post_if_due(client, llm):
    should_post_flag, new_ts, digest_type = should_post()
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
    if not trends:
        return False

    signature = "\n\nQwen | Chainbase TOPS 💜💛"

    if digest_type == "full":
        header = "TOP TREND:\n\n"
        item = trends[0]
        keyword = item.get("keyword", "")
        score = item.get("score", 0)
        rank_status = item.get("rank_status", "same")
        trend_emoji = get_trend_emoji(rank_status)

        raw_line = f"- {keyword} [score:{int(score)}]: {item.get('summary', '')}"
        final_line = generator.generate_digest(llm, raw_line)
        if final_line.startswith("- "):
            final_line = final_line[2:]

        final_line = f"{trend_emoji} {final_line}"

        max_content_len = 300 - len(header) - len(signature)
        if len(final_line) > max_content_len:
            final_line = final_line[:max_content_len].rsplit(' ', 1)[0]

        post_text = header + final_line + signature

    else:
        header = "TOP TRENDS:\n\n"
        base_len = len(header) + len(signature)
        lines = []

        for item in trends:
            keyword = item.get("keyword", "Unknown")
            score = item.get("score", 0)
            rank_status = item.get("rank_status", "same")
            trend_emoji = get_trend_emoji(rank_status)
            line = f"{trend_emoji} {keyword} [{int(score)}]"

            test_content = "\n".join(lines + [line])
            if len(header) + len(test_content) + len(signature) <= 300:
                lines.append(line)
            else:
                break

        if not lines:
            return False
        post_text = header + "\n".join(lines) + signature

    try:
        resp = await bsky.post_root(client, BOT_DID, post_text)
        new_uri = resp.get("uri")
        if new_uri:
            state.save_last_digest_uri(new_uri)
        state.save_daily_post_ts(new_ts)
        logger.info(f"Posted {digest_type} digest")
        return True
    except Exception as e:
        logger.error(f"[NEWS] Post failed: {e}")
        return False
