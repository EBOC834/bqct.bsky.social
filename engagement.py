import os
import asyncio
import logging
import bsky
import generator

logger = logging.getLogger(__name__)
BOT_DID = os.getenv("BOT_DID")
MAX_COMMENTS = 50

async def process_digest_engagement(client, llm, digest_uri, digest_text):
    try:
        token = client.headers.get("Authorization", "").replace("Bearer ", "")
        r = await client.get(
            f"https://bsky.social/xrpc/app.bsky.feed.getPostThread?uri={digest_uri}&depth=1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30
        )
        if r.status_code != 200:
            logger.warning(f"Failed to fetch digest thread: {r.status_code}")
            return

        data = r.json()
        replies = data.get("thread", {}).get("replies", [])
        if not replies:
            logger.info("No comments on digest.")
            return

        comments = []
        for rep in replies[:MAX_COMMENTS]:
            post = rep.get("post", {})
            record = post.get("record", {})
            if record and record.get("text"):
                comments.append({
                    "uri": post.get("uri"),
                    "cid": post.get("cid"),
                    "handle": post.get("author", {}).get("handle", "unknown"),
                    "text": record.get("text", "")[:300]
                })

        if not comments:
            logger.info("No valid comments found.")
            return

        plan = generator.generate_engagement_plan(llm, digest_text, comments)
        if not plan:
            logger.info("LLM selected no comments for engagement.")
            return

        logger.info(f"Engaging with {len(plan)} comments...")
        for item in plan:
            idx = item.get("index")
            reply_text = item.get("reply", "")
            if idx is None or idx >= len(comments) or not reply_text:
                continue

            target = comments[idx]
            try:
                await bsky.like_post(client, BOT_DID, target["uri"], target["cid"])
                logger.info(f"Liked comment {idx}")
                await bsky.post_reply(client, BOT_DID, reply_text, digest_uri, data["thread"]["post"]["cid"], target["uri"], target["cid"])
                logger.info(f"Replied to comment {idx}")
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"Engagement failed for comment {idx}: {e}")
                continue
    except Exception as e:
        logger.error(f"Engagement process error: {e}")
