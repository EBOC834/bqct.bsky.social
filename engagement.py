import os
import logging
import json
import bsky
import generator
from config import BOT_DID

logger = logging.getLogger(__name__)
MAX_COMMENTS_FOR_ENGAGEMENT = 50

async def process_digest_engagement(client, llm, digest_uri, digest_text):
    logger.info(f"[ENGAGEMENT] Processing digest: {digest_uri[:50]}...")
    
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    thread = await bsky.get_thread_raw(client, digest_uri, token)
    if not thread:
        logger.warning("[ENGAGEMENT] Failed to fetch thread")
        return
    
    comments = []
    root = thread.get("thread", {})
    replies = root.get("replies", [])
    
    def extract_replies(reply_list):
        extracted = []
        for item in reply_list:
            if not isinstance(item, dict):
                continue
            post = item.get("post")
            if not post:
                continue
            author = post.get("author", {})
            did = author.get("did", "")
            if did == BOT_DID:
                continue
            record = post.get("record", {})
            extracted.append({
                "uri": post.get("uri"),
                "cid": post.get("cid"),
                "handle": author.get("handle", ""),
                "text": record.get("text", ""),
                "indexedAt": post.get("indexedAt", "")
            })
            if "replies" in item:
                extracted.extend(extract_replies(item["replies"]))
        return extracted
        
    comments = extract_replies(replies)
    
    if not comments:
        logger.info("[ENGAGEMENT] No comments to process")
        return
    
    if len(comments) > MAX_COMMENTS_FOR_ENGAGEMENT:
        comments.sort(key=lambda c: c.get("indexedAt", ""), reverse=True)
        comments = comments[:MAX_COMMENTS_FOR_ENGAGEMENT]
        logger.warning(f"[ENGAGEMENT] Thread exceeds limit. Processing {MAX_COMMENTS_FOR_ENGAGEMENT} latest comments.")
    
    plan = await generator.generate_engagement_plan(llm, digest_text, comments)
    likes = plan.get("likes", [])
    replies_to_send = plan.get("replies", [])
    
    for uri in likes:
        try:
            comment = next((c for c in comments if c["uri"] == uri), None)
            if comment:
                await bsky.like_post(client, BOT_DID, uri, comment["cid"])
                logger.info(f"[ENGAGEMENT] Liked: @{comment['handle']}")
        except Exception as e:
            logger.error(f"[ENGAGEMENT] Like failed for {uri}: {e}")
    
    for reply_plan in replies_to_send:
        uri = reply_plan.get("uri")
        text = reply_plan.get("text", "")
        if not uri or not text:
            continue
        try:
            comment = next((c for c in comments if c["uri"] == uri), None)
            if comment:
                parent_cid = comment["cid"]
                root_parts = digest_uri.split("/")
                root_cid = root_parts[-1] if len(root_parts) > 4 else ""
                await bsky.post_reply(client, BOT_DID, text, digest_uri, root_cid, uri, parent_cid)
                logger.info(f"[ENGAGEMENT] Replied to @{comment['handle']}: {text[:50]}...")
        except Exception as e:
            logger.error(f"[ENGAGEMENT] Reply failed for {uri}: {e}")
    
    logger.info(f"[ENGAGEMENT] Completed: {len(likes)} likes, {len(replies_to_send)} replies")
