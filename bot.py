import os
import json
import asyncio
import re
import logging
import config
import state
import search
import generator
import bsky
import news
import parser
import engagement
from config import BOT_HANDLE, BOT_PASSWORD, BOT_DID, OWNER_DID

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TRIGGER_KEYWORDS = ["!t", "!c", "!s", "!r"]

async def process_item(client, item, llm):
    uri, user_text = item["uri"], item["text"]
    do_search, search_type = item.get("has_search", False), item.get("search_type", "tavily")
    
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    chain_data = await bsky.fetch_thread_chain(client, uri, token)
    if not chain_data:
        logger.warning("Thread chain fetch failed, skipping.")
        return
    
    root_uri = chain_data["root_uri"]
    root_cid = chain_data["root_cid"]
    parent_cid = chain_data["parent_cid"]
    chain = chain_data["chain"]
    
    root_post = None
    relevant_posts = []
    link_cache = {}
    
    for idx, post in enumerate(chain):
        rec = post.get("record", {})
        author = post.get("author", {})
        did = author.get("did", "")
        handle = author.get("handle", "")
        text = rec.get("text", "")
        embed = rec.get("embed")
        
        embed_text, alts = parser._extract_embed_full(embed) if embed else ("", [])
        link_hints = []
        urls = re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)
        for url in urls:
            if url not in link_cache:
                clean = await parser._extract_clean_url_content(url)
                link_cache[url] = clean
                if clean:
                    link_hints.append(f"[Page content: {clean[:400]}]")
        
        post_data = {
            "uri": post.get("uri"),
            "handle": handle,
            "did": did,
            "text": text,
            "embed": embed_text,
            "link_hints": link_hints,
            "alts": alts,
            "is_root": (idx == 0)
        }
        
        if idx == 0:
            root_post = post_data
        elif did == OWNER_DID:
            text_lower = text.lower()
            if any(trigger in text_lower for trigger in TRIGGER_KEYWORDS):
                relevant_posts.append(post_data)
    
    thread_id = root_uri
    memory = state.load_context(thread_id)
    
    search_results = ""
    if do_search:
        root_text = root_post.get("text", "") if root_post else ""
        search_params = generator.extract_search_params(llm, user_text, root_text)
        provider = search.SEARCH_PROVIDERS.get(search_type)
        if provider:
            func = provider["func"]
            supported = provider.get("supports", [])
            kwargs = {k: v for k, v in search_params.items() if k in supported}
            kwargs.pop('query', None)
            search_results = await func(search_params["query"], **kwargs)
            if not search.is_search_result_valid(search_results, search_type):
                search_results = ""
    
    full_context = state.merge_contexts(root_post, relevant_posts[:10], memory, search_results, user_text)
    reply = generator.get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type)
    reply = generator.add_signature(reply, search_type if do_search else None)
    
    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        logger.info("Reply posted successfully.")
    except Exception as e:
        logger.error(f"Failed to post reply: {e}")
        return
    
    new_summary = generator.update_summary(llm, memory, user_text, reply)
    state.save_context(thread_id, new_summary)

async def main():
    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
            logger.info("Authenticated with Bluesky API")
        except Exception as e:
            logger.error(f"Auth failed: {e}")
            return
        digest_due, _ = news.check_mini_timer()
        has_notifications = os.path.exists("work_data.json")
        llm = None
        if digest_due or has_notifications:
            try:
                llm = generator.get_model()
                logger.info("Model loaded successfully.")
            except Exception as e:
                logger.error(f"Model load failed, skipping heavy tasks: {e}")
                return
        if llm:
            active_uri = state.load_active_digest_uri()
            if active_uri and active_uri not in ("{}", "null", ""):
                try:
                    active_rec = await bsky.get_record(client, active_uri)
                    active_text = active_rec["value"].get("text", "") if active_rec else ""
                    logger.info("Processing active digest engagement...")
                    await engagement.process_digest_engagement(client, llm, active_uri, active_text)
                except Exception as e:
                    logger.error(f"Failed to process active digest engagement: {e}")
            if digest_due and llm:
                logger.info("Posting daily digest...")
                await news.post_if_due(client, llm)
            if has_notifications and llm:
                logger.info("Processing notifications...")
                with open("work_data.json", "r") as f:
                    work_data = json.load(f)
                if work_data.get("items"):
                    for item in work_data["items"]:
                        await process_item(client, item, llm)
                        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
