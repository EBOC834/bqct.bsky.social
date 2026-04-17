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
            logger.info("LLM returned empty plan.")
            return

        reply_items = plan.get("replies", [])
        like_items = plan.get("likes", [])
        processed = set()

        actions = []
        for item in like_items:
            idx = item.get("index")
            if idx is not None and 0 <= idx < len(comments) and idx not in processed:
                actions.append(("like", idx, None))
                processed.add(idx)

        for item in reply_items:
            idx = item.get("index")
            reply_text = item.get("reply", "")
            if idx is not None and 0 <= idx < len(comments) and idx not in processed and reply_text:
                actions.append(("reply", idx, reply_text))
                processed.add(idx)

        if not actions:
            logger.info("No actions selected by LLM.")
            return

        logger.info(f"Executing {len(actions)} engagement actions...")
        root_cid = data["thread"]["post"]["cid"]
        for action_type, idx, reply_text in actions:
            target = comments[idx]
            try:
                await bsky.like_post(client, BOT_DID, target["uri"], target["cid"])
                logger.info(f"Liked comment {idx}")
                if action_type == "reply":
                    await bsky.post_reply(client, BOT_DID, reply_text, digest_uri, root_cid, target["uri"], target["cid"])
                    logger.info(f"Replied to comment {idx}")
                await asyncio.sleep(1.5)
            except Exception as e:
                logger.error(f"Action failed for comment {idx}: {e}")
                continue
    except Exception as e:
        logger.error(f"Engagement process error: {e}")
