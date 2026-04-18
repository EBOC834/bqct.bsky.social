import logging
import bsky
import generator
from config import BOT_DID

logger = logging.getLogger(__name__)

def _flatten_comments(node, out=None):
    if out is None: out = []
    if not node or node.get("$type") in ["app.bsky.feed.defs#notFoundPost", "app.bsky.feed.defs#blockedPost"]:
        return out
    post = node.get("post", {})
    record = post.get("record", {})
    if not record: return out
    out.append({"uri": post.get("uri", ""), "cid": post.get("cid", ""), "handle": post.get("author", {}).get("handle", ""), "text": record.get("text", "")})
    for r in node.get("replies", []):
        if isinstance(r, dict): _flatten_comments(r, out)
    return out

async def process_digest_engagement(client, llm, digest_uri: str, digest_text: str):
    logger.info(f"[ENGAGEMENT] Processing digest: {digest_uri}")
    try:
        token = client.headers.get("Authorization", "").replace("Bearer ", "")
        thread = await bsky.get_thread_raw(client, digest_uri, token)
        if not thread: return
        comments = _flatten_comments(thread.get("thread", {}))
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
                await bsky.post_reply(client, BOT_DID, reply.get("text", ""), digest_uri, "", c["uri"], c["cid"])
            except Exception as e: logger.error(f"[ENGAGEMENT] Failed to reply to {reply.get('uri')}: {e}")
    except Exception as e: logger.error(f"[ENGAGEMENT] Failed: {e}")
