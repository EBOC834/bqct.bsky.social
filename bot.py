import os
import json
import asyncio
import logging
import config
import state
import search
import generator
import bsky
import news
import parser

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
BOT_DID = os.getenv("BOT_DID")

async def process_item(client, item, llm):
    uri, user_text = item["uri"], item["text"]
    do_search, search_type = item.get("has_search", False), item.get("search_type", "tavily")
    
    logger.info(f"[1] Processing request for URI: {uri}, Text: {user_text[:50]}...")
    
    rec = await bsky.get_record(client, uri)
    if not rec:
        logger.warning("Record not found, skipping.")
        return
    
    reply_info = rec["value"].get("reply", {})
    root_uri = reply_info.get("root", {}).get("uri", uri)
    root_cid = reply_info.get("root", {}).get("cid", "")
    parent_cid = rec.get("cid", "")
    thread_id = root_uri
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    
    raw_thread = await bsky.get_thread_raw(client, root_uri, token)
    posts = await parser.parse_thread(raw_thread, token, client) if raw_thread else []
    
    root_post = next((p for p in posts if p.get("is_root")), {})
    
    if not root_post and root_uri != uri:
        root_rec = await bsky.get_record(client, root_uri)
        if root_rec:
            root_author = root_rec.get("author", {})
            root_post = {
                "handle": root_author.get("handle", ""),
                "text": root_rec["value"].get("text", ""),
                "is_root": True,
                "uri": root_rec.get("uri", ""),
                "embed": "",
                "link_hints": [],
                "alts": []
            }
            logger.info(f"[FALLBACK] Loaded ROOT from get_record")
    
    recent_posts = [p for p in posts if not p.get("is_root")][:10]
    
    bsky_context_str = "\n".join([
        f"@{p.get('handle', 'unknown')}: {p.get('text', '')}" 
        for p in ([root_post] if root_post else []) + recent_posts
    ])
    logger.info(f"[2] Bluesky Context Fetched:\n{bsky_context_str if bsky_context_str else '(Empty - API error)'}")
    
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
            
            logger.info(f"[3] Search Query Generated: {search_params['query']} | Provider: {search_type}")
            search_results = await func(search_params["query"], **kwargs)
            
            if not search.is_search_result_valid(search_results, search_type):
                search_results = ""
                logger.warning("Search result invalid, cleared.")
            else:
                logger.info(f"[4] Search Response Received ({len(search_results)} chars)")
    
    full_context = state.merge_contexts(root_post, recent_posts, memory, search_results, user_text)
    logger.info(f"[5] Final Context Assembled:\n{full_context}")
    
    reply = generator.get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type)
    logger.info(f"[7] Final Reply to Bluesky:\n{reply}")
    
    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        logger.info("Reply posted successfully.")
    except Exception as e:
        logger.error(f"Failed to post reply: {e}")
        return
        
    new_summary = generator.update_summary(llm, memory, user_text, reply)
    logger.info(f"[6] Saving Context to Secret:\n{new_summary}")
    state.save_context(thread_id, new_summary)

async def main():
    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
            logger.info("Authenticated with Bluesky API")
        except Exception as e:
            logger.error(f"Auth failed: {e}")
            return
        
        digest_due, _, _ = news.should_post()
        has_notifications = os.path.exists("work_data.json")
        
        llm = None
        if digest_due or has_notifications:
            try:
                llm = generator.get_model()
                logger.info("Model loaded successfully.")
            except Exception as e:
                logger.error(f"Model load failed, skipping heavy tasks: {e}")
                return
        
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
