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
    
    logger.info(f"[PROCESS] START | uri={uri} | user_text={user_text[:50]}... | do_search={do_search} | search_type={search_type}")
    
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    chain_data = await bsky.fetch_thread_chain(client, uri, token)
    if not chain_data:
        logger.warning("Thread chain fetch failed, skipping.")
        return
    
    root_uri = chain_data["root_uri"]
    root_cid = chain_data["root_cid"]
    parent_cid = chain_data["parent_cid"]
    chain = chain_data["chain"]
    
    logger.debug(f"[CHAIN] Loaded | root_uri={root_uri} | parent_cid={parent_cid[:10] if parent_cid else '(empty)'}... | posts_count={len(chain)}")
    
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
            logger.debug(f"[CHAIN] Root post | handle={handle} | text_preview={text[:50]}...")
        elif did == OWNER_DID:
            text_lower = text.lower()
            if any(trigger in text_lower for trigger in TRIGGER_KEYWORDS):
                relevant_posts.append(post_data)
                logger.debug(f"[CHAIN] Relevant owner post | trigger found | text={text[:50]}...")
    
    thread_id = root_uri
    memory = state.load_context(thread_id)
    logger.info(f"[CONTEXT] Memory loaded | thread_id={thread_id} | memory_len={len(memory)} | preview={memory[:100] if memory else '(empty)'}...")
    
    search_results = ""
    if do_search:
        root_text = root_post.get("text", "") if root_post else ""
        logger.info(f"[SEARCH] Triggered | root_text='{root_text[:50]}...' | user_text='{user_text[:50]}...'")
        search_params = generator.extract_search_params(llm, user_text, root_text)
        logger.info(f"[SEARCH] Params extracted: query='{search_params.get('query')}' | time_range={search_params.get('time_range')} | topic={search_params.get('topic')}")
        provider = search.SEARCH_PROVIDERS.get(search_type)
        if provider:
            func = provider["func"]
            supported = provider.get("supports", [])
            kwargs = {k: v for k, v in search_params.items() if k in supported and v}
            kwargs.pop('query', None)
            logger.info(f"[SEARCH] Calling {search_type} | query='{search_params['query']}' | kwargs={kwargs}")
            search_results = await func(search_params["query"], **kwargs)
            if search.is_search_result_valid(search_results, search_type):
                logger.info(f"[SEARCH] Success | results_len={len(search_results)}")
                logger.debug(f"[SEARCH] Results preview: {search_results[:300]}...")
            else:
                logger.warning("[SEARCH] Invalid results, cleared")
                search_results = ""
        else:
            logger.warning(f"[SEARCH] Unknown provider: {search_type}")
    
    logger.info(f"[CONTEXT] Building final context | root_post={bool(root_post)} | relevant_posts={len(relevant_posts)} | memory={bool(memory)} | search_results={bool(search_results)}")
    full_context = state.merge_contexts(root_post, relevant_posts[:10], memory, search_results, user_text)
    logger.info(f"[CONTEXT] Final context:\n{full_context}")
    
    logger.info(f"[LLM] Generating answer | do_search={do_search} | search_type={search_type}")
    reply = generator.get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type)
    reply = generator.add_signature(reply, search_type if do_search else None)
    logger.info(f"[REPLY] Final reply:\n{reply}")
    
    logger.debug(f"[POST] Calling post_reply | bot_did={BOT_DID} | reply_len={len(reply)} | root_uri={root_uri} | root_cid={root_cid[:10] if root_cid else '(empty)'}... | parent_uri={uri} | parent_cid={parent_cid[:10] if parent_cid else '(empty)'}...")
    try:
        result = await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        logger.info("Reply posted successfully.")
        logger.debug(f"[POST] Response | result_keys={list(result.keys()) if result else None}")
    except Exception as e:
        logger.error(f"Failed to post reply: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return
    
    new_summary = generator.update_summary(llm, memory, user_text, reply)
    logger.info(f"[MEMORY] Saving context | thread_id={thread_id} | summary_len={len(new_summary)} | preview={new_summary[:100]}...")
    state.save_context(thread_id, new_summary)
    logger.info(f"[PROCESS] END | status=SUCCESS")

async def main():
    logger.info("=== BOT STARTED ===")
    logger.debug(f"[ENV] BOT_HANDLE={BOT_HANDLE} | BOT_DID={BOT_DID} | OWNER_DID={OWNER_DID}")
    logger.debug(f"[CONFIG] MODEL_PATH={config.MODEL_PATH} | MODEL_N_CTX={config.MODEL_N_CTX} | CONTEXT_SLOT_COUNT={config.CONTEXT_SLOT_COUNT}")
    
    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
            logger.info("Authenticated with Bluesky API")
            logger.debug(f"[AUTH] Client headers | Authorization={'Bearer ***' if 'Authorization' in client.headers else 'MISSING'}")
        except Exception as e:
            logger.error(f"Auth failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return
        
        digest_due, _ = news.check_mini_timer()
        logger.debug(f"[DIGEST] check_mini_timer | digest_due={digest_due}")
        
        has_notifications = os.path.exists("work_data.json")
        logger.debug(f"[NOTIF] work_data.json exists={has_notifications}")
        
        llm = None
        if digest_due or has_notifications:
            logger.debug("[LLM] Loading model")
            try:
                llm = generator.get_model()
                logger.info("Model loaded successfully.")
                logger.debug(f"[LLM] Config | model_path={config.MODEL_PATH} | n_ctx={config.MODEL_N_CTX} | n_threads={config.MODEL_N_THREADS}")
            except Exception as e:
                logger.error(f"Model load failed, skipping heavy tasks: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                return
        
        if llm:
            active_uri = state.load_active_digest_uri()
            logger.debug(f"[DIGEST] active_uri={active_uri}")
            if active_uri and active_uri not in ("{}", "null", ""):
                try:
                    active_rec = await bsky.get_record(client, active_uri)
                    active_text = active_rec["value"].get("text", "") if active_rec else ""
                    logger.info("Processing active digest engagement...")
                    await engagement.process_digest_engagement(client, llm, active_uri, active_text)
                    logger.debug("[ENGAGEMENT] Completed")
                except Exception as e:
                    logger.error(f"Failed to process active digest engagement: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
            
            if digest_due and llm:
                logger.info("Posting daily digest...")
                await news.post_if_due(client, llm)
                logger.debug("[DIGEST] post_if_due completed")
            
            if has_notifications and llm:
                logger.info("Processing notifications...")
                with open("work_data.json", "r") as f:
                    work_data = json.load(f)
                logger.debug(f"[NOTIF] work_data.json loaded | items_count={len(work_data.get('items', []))}")
                if work_data.get("items"):
                    for idx, item in enumerate(work_data["items"], 1):
                        logger.debug(f"[NOTIF] Processing item {idx}/{len(work_data['items'])} | uri={item.get('uri')}")
                        await process_item(client, item, llm)
                        logger.debug(f"[NOTIF] Item {idx} completed")
                        await asyncio.sleep(1)
        
        logger.info("=== BOT FINISHED ===")

if __name__ == "__main__":
    asyncio.run(main())
