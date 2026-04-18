import os
import logging
import re
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

def smart_truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    truncated = text[:max_len - 3]
    for sep in ['.', '!', '?', ';']:
        idx = truncated.rfind(sep)
        if idx > max_len * 0.7:
            return truncated[:idx + 1] + "..."
    return truncated.rsplit(' ', 1)[0] + "..."

def check_timer(secret_name, hours):
    now_utc = datetime.now(timezone.utc)
    raw = os.getenv(secret_name, "").strip()
    if not raw or raw == "{}" or raw == "null":
        return True, now_utc.isoformat()
    try:
        last_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        diff = now_utc - last_ts
        if diff.total_seconds() >= hours * 3600:
            return True, now_utc.isoformat()
        return False, raw
    except Exception:
        return True, now_utc.isoformat()

def check_mini_timer():
    return check_timer("LAST_NEWS", 1)

async def post_if_due(client, llm):
    do_full, full_ts = check_timer("LAST_FULL_DIGEST", 1)
    do_mini, mini_ts = check_timer("LAST_MINI_DIGEST", 3)
    if not do_full and not do_mini:
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
    now_utc = datetime.now(timezone.utc).isoformat()
    if do_mini:
        header = "TOP CRYPTO TRENDS:\n\n"
        lines = []
        for item in trends:
            keyword = item.get("keyword", "Unknown")
            score = item.get("score", 0)
            rank_status = item.get("rank_status", "same")
            trend_emoji = get_trend_emoji(rank_status)
            line = f"{trend_emoji} {keyword} 📊 {int(score)}"
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
                state.save_active_digest_uri(new_uri)
                state.save_daily_post_ts(now_utc)
                state._write_secret("LAST_MINI_DIGEST", now_utc)
                state._write_secret("LAST_FULL_DIGEST", now_utc)
            logger.info("Posted mini digest")
            return True
        except Exception as e:
            logger.error(f"Mini post failed: {e}")
            return False
    elif do_full:
        header = "TOP CRYPTO TREND:\n\n"
        item = trends[0]
        keyword = item.get("keyword", "")
        score = int(item.get("score", 0))
        rank_status = item.get("rank_status", "same")
        trend_emoji = get_trend_emoji(rank_status)
        summary = item.get("summary", "")
        score_suffix = f"\n📊 {score}"
        max_text_chars = 248 - len(score_suffix)
        raw_input = f"{keyword}: {summary}"
        final_text = generator.generate_digest(llm, raw_input, max_chars=max_text_chars)
        final_text = re.sub(r'\s*🆕\s*:?\s*\d+\s*$', '', final_text)
        final_text = re.sub(r'\s*\[score:\s*\d+\]\s*:', ':', final_text)
        final_line = f"{trend_emoji} {final_text}{score_suffix}"
        max_content_len = 300 - len(header) - len(signature)
        final_line = smart_truncate(final_line, max_content_len)
        post_text = header + final_line + signature
        try:
            resp = await bsky.post_root(client, BOT_DID, post_text)
            new_uri = resp.get("uri")
            if new_uri:
                state.save_last_digest_uri(new_uri)
                state.save_active_digest_uri(new_uri)
                state.save_daily_post_ts(now_utc)
                state._write_secret("LAST_FULL_DIGEST", now_utc)
            logger.info("Posted full digest")
            return True
        except Exception as e:
            logger.error(f"Full post failed: {e}")
            return False
    return False
