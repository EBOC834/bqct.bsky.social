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
        posts = await parser.parse_thread(thread, token, client)
        comments = [p for p in posts if not p.get("is_root") and p.get("uri") != digest_uri]
        logger.info(f"[ENGAGEMENT] Found {len(comments)} comments")
        if not comments: return
        for i, comment in enumerate(comments, 1):
            author_handle = comment.get("handle", "unknown")
            comment_text = comment.get("text", "")
            comment_uri = comment.get("uri", "")
            logger.info(f"[ENGAGEMENT] Comment {i}: @{author_handle} - \"{comment_text[:100]}...\"")
        engagement_plan = await generator.generate_engagement_plan(llm, digest_text, comments)
        logger.info(f"[ENGAGEMENT] Plan generated: {engagement_plan}")
        likes_to_give = engagement_plan.get("likes", [])
        replies_to_make = engagement_plan.get("replies", [])
        for comment_uri in likes_to_give:
            try:
                rec = await bsky.get_record(client, comment_uri)
                cid = rec.get("cid", "") if rec else ""
                await bsky.like_post(client, BOT_DID, comment_uri, cid)
                logger.info(f"[ENGAGEMENT] Liked: {comment_uri}")
            except Exception as e:
                logger.error(f"[ENGAGEMENT] Failed to like {comment_uri}: {e}")
        for reply_data in replies_to_make:
            try:
                comment_uri = reply_data.get("uri")
                reply_text = reply_data.get("text", "")
                comment_record = await bsky.get_record(client, comment_uri)
                if not comment_record: continue
                root_uri = comment_record["value"].get("reply", {}).get("root", {}).get("uri", digest_uri)
                parent_uri = comment_uri
                root_cid = comment_record.get("cid", "")
                await bsky.post_reply(client, BOT_DID, reply_text, root_uri, root_cid, comment_uri, parent_uri)
                logger.info(f"[ENGAGEMENT] Replied to {comment_uri}: \"{reply_text[:80]}...\"")
            except Exception as e:
                logger.error(f"[ENGAGEMENT] Failed to reply to {comment_uri}: {e}")
        logger.info("[ENGAGEMENT] Processing complete")
    except Exception as e:
        logger.error(f"[ENGAGEMENT] Failed to process engagement: {e}")
