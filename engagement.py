import logging
from typing import List, Dict
import bsky
import generator

logger = logging.getLogger(__name__)

async def process_digest_engagement(client, llm, digest_uri: str, digest_text: str):
    logger.info(f"[ENGAGEMENT] Processing digest: {digest_uri}")
    
    try:
        thread = await bsky.get_thread_raw(client, digest_uri, client.headers.get("Authorization", "").replace("Bearer ", ""))
        posts = await bsky.parse_thread(thread, client.headers.get("Authorization", "").replace("Bearer ", ""), client)
        
        comments = [p for p in posts if not p.get("is_root") and p.get("uri") != digest_uri]
        logger.info(f"[ENGAGEMENT] Found {len(comments)} comments")
        
        if not comments:
            logger.info("[ENGAGEMENT] No comments to process")
            return
        
        for i, comment in enumerate(comments, 1):
            author_handle = comment.get("handle", "unknown")
            comment_text = comment.get("text", "")
            comment_uri = comment.get("uri", "")
            
            logger.info(f"[ENGAGEMENT] Comment {i}: @{author_handle} - \"{comment_text[:100]}...\"")
        
        engagement_plan = await generator.generate_engagement_plan(llm, digest_text, comments)
        logger.info(f"[ENGAGEMENT] Plan generated: {engagement_plan}")
        
        likes_to_give = engagement_plan.get("likes", [])
        replies_to_make = engagement_plan.get("replies", [])
        
        logger.info(f"[ENGAGEMENT] Likes to give: {len(likes_to_give)}")
        for like_uri in likes_to_give:
            logger.info(f"  - {like_uri}")
        
        logger.info(f"[ENGAGEMENT] Replies to make: {len(replies_to_make)}")
        for reply in replies_to_make:
            logger.info(f"  - To: {reply.get('uri')} | Text: {reply.get('text', '')[:80]}...")
        
        for comment_uri in likes_to_give:
            try:
                await bsky.like_post(client, comment_uri)
                logger.info(f"[ENGAGEMENT] ✅ Liked: {comment_uri}")
            except Exception as e:
                logger.error(f"[ENGAGEMENT] ❌ Failed to like {comment_uri}: {e}")
        
        for reply_data in replies_to_make:
            try:
                comment_uri = reply_data.get("uri")
                reply_text = reply_data.get("text", "")
                
                comment_record = await bsky.get_record(client, comment_uri)
                if not comment_record:
                    logger.warning(f"[ENGAGEMENT] Comment not found: {comment_uri}")
                    continue
                
                root_uri = comment_record["value"].get("reply", {}).get("root", {}).get("uri", digest_uri)
                parent_uri = comment_uri
                
                await bsky.post_reply(client, bsky.BOT_DID, reply_text, root_uri, "", comment_uri, parent_uri)
                logger.info(f"[ENGAGEMENT] ✅ Replied to {comment_uri}: \"{reply_text[:80]}...\"")
            except Exception as e:
                logger.error(f"[ENGAGEMENT] ❌ Failed to reply to {comment_uri}: {e}")
        
        logger.info("[ENGAGEMENT] Processing complete")
        
    except Exception as e:
        logger.error(f"[ENGAGEMENT] Failed to process engagement: {e}")
        import traceback
        logger.error(traceback.format_exc())
