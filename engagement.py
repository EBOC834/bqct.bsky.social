import logging
import bsky
import parser
import generator
from config import BOT_DID

logger = logging.getLogger(__name__)

async def process_digest_engagement(client, llm, digest_uri: str, digest_text: str):
    logger.info(f"[ENGAGEMENT] Processing digest: {digest_uri}")
    try:
        token = client.headers.get("Authorization", "").replace("Bearer ", "")
        thread = await bsky.get_thread_raw(client, digest_uri, token)
        if not thread: return
        posts = await parser.parse_thread(thread, token, client)
        comments = [p for p in posts if not p.get("is_root") and p.get("uri") != digest_uri]
        if not comments: return
        plan = await generator.generate_engagement_plan(llm, digest_text, comments)
        for uri in plan.get("likes", []):
            try:
                c = next((x for x in comments if x["uri"] == uri), None)
                if c: await bsky.like_post(client, BOT_DID, uri, c["cid"])
            except Exception as e: logger.error(f"[ENGAGEMENT] Failed to like {uri}: {e}")
        for reply in plan.get("replies", []):
            try:
                c = next((x for x in comments if x["uri"] == reply.get("uri")), None)
                if not c: continue
                root_uri = reply.get("root_uri", digest_uri)
                await bsky.post_reply(client, BOT_DID, reply.get("text", ""), root_uri, "", c["uri"], c["cid"])
            except Exception as e: logger.error(f"[ENGAGEMENT] Failed to reply to {reply.get('uri')}: {e}")
    except Exception as e: logger.error(f"[ENGAGEMENT] Failed: {e}")
