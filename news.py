import os
import logging
import re
from datetime import datetime, timezone
import state
import search
import bsky
import generator
import engagement
from config import BOT_DID

logger = logging.getLogger(__name__)

def get_trend_emoji(rank_status: str) -> str:
    status = (rank_status or "same").lower()
    if status == "new": return "🆕"
    elif status == "up": return "↗️"
    elif status == "down": return "↘️"
    return "➡️"

def smart_truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len: return text
    truncated = text[:max_len - 3]
    for sep in ['.', '!', '?', ';']:
        idx = truncated.rfind(sep)
        if idx > max_len * 0.7: return truncated[:idx + 1] + "..."
    return truncated.rsplit(' ', 1)[0] + "..."

def check_timer(secret_name: str, hours: int) -> tuple[bool, str]:
    now_utc = datetime.now(timezone.utc)
    raw = os.getenv(secret_name, "").strip()
    if not raw or raw in ("{}", "null"): return True, now_utc.isoformat()
    try:
        last_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        diff = now_utc - last_ts
        if diff.total_seconds() >= hours * 3600: return True, now_utc.isoformat()
        return False, raw
    except: return True, now_utc.isoformat()

def check_mini_timer() -> tuple[bool, str]:
    return check_timer("LAST_NEWS", 1)

async def post_if_due(client, llm) -> bool:
    do_full, _ = check_timer("LAST_FULL_DIGEST", 1)
    do_mini, _ = check_timer("LAST_MINI_DIGEST", 3)
    if not do_full and not do_mini: return False
    last_digest_uri = state.load_last_digest_uri()
    if last_digest_uri and llm:
        try:
            last_rec = await bsky.get_record(client, last_digest_uri)
            last_digest_text = last_rec["value"].get("text", "") if last_rec else ""
            await engagement.process_digest_engagement(client, llm, last_digest_uri, last_digest_text)
        except Exception as e: logger.error(f"Previous digest engagement failed: {e}")
    trends = await search.chainbase_search("")
    if not trends: return False
    signature = "\nQwen | Chainbase TOPS 💜💛"
    now_utc = datetime.now(timezone.utc).isoformat()
    if do_mini:
        header = "TOP CRYPTO TRENDS:\n"
        lines = []
        for item in trends:
            line = f"{get_trend_emoji(item.get('rank_status', 'same'))} {item.get('keyword', 'Unknown')} 📊 {int(item.get('score', 0))}"
            if len(header) + len("\n".join(lines + [line])) + len(signature) <= 300: lines.append(line)
            else: break
        if not lines: return False
        post_text = header + "\n".join(lines) + signature
        try:
            resp = await bsky.post_root(client, BOT_DID, post_text)
            if new_uri := resp.get("uri"):
                state.save_last_digest_uri(new_uri)
                state.save_active_digest_uri(new_uri)
                state.save_daily_post_ts(now_utc)
                state._write_secret("LAST_MINI_DIGEST", now_utc)
                state._write_secret("LAST_FULL_DIGEST", now_utc)
            return True
        except Exception as e:
            logger.error(f"Mini post failed: {e}")
            return False
    elif do_full:
        item = trends[0]
        score_suffix = f"\n📊 {int(item.get('score', 0))}"
        final_text = generator.generate_digest(llm, f"{item.get('keyword', '')}: {item.get('summary', '')}", max_chars=248 - len(score_suffix))
        final_line = f"{get_trend_emoji(item.get('rank_status', 'same'))} {final_text}{score_suffix}"
        final_line = smart_truncate(final_line, 300 - len("TOP CRYPTO TREND:\n") - len(signature))
        post_text = "TOP CRYPTO TREND:\n" + final_line + signature
        try:
            resp = await bsky.post_root(client, BOT_DID, post_text)
            if new_uri := resp.get("uri"):
                state.save_last_digest_uri(new_uri)
                state.save_active_digest_uri(new_uri)
                state.save_daily_post_ts(now_utc)
                state._write_secret("LAST_FULL_DIGEST", now_utc)
            return True
        except Exception as e:
            logger.error(f"Full post failed: {e}")
            return False
    return False
