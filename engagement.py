import logging
import bsky
import generator
from config import BOT_DID

logger = logging.getLogger(__name__)

async def process_digest_engagement(client, llm, digest_uri: str, digest_text: str):
    logger.info(f"[ENGAGEMENT] Processing digest: {digest_uri}")
    try:
        token = client.headers.get("Authorization", "").replace("Bearer ", "")
        thread = await bsky.fetch_thread(client, digest_uri)
        if not thread: return
        comments = [{"uri": digest_uri, "text": thread["root_text"], "cid": thread["root_cid"]}]
        if not comments: return
        plan = await generator.generate_engagement_plan(llm, digest_text, comments)
        for uri in plan.get("likes", []):
            try: await bsky.like_post(client, BOT_DID, uri, thread["root_cid"])
            except Exception as e: logger.error(f"[ENGAGEMENT] Like failed: {e}")
        for reply in plan.get("replies", []):
            try:
                await bsky.post_reply(client, BOT_DID, reply.get("text", ""), digest_uri, thread["root_cid"], digest_uri, thread["root_cid"])
            except Exception as e: logger.error(f"[ENGAGEMENT] Reply failed: {e}")
    except Exception as e: logger.error(f"[ENGAGEMENT] Failed: {e}")
