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

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
BOT_DID = os.getenv("BOT_DID")
OWNER_DID = os.getenv("OWNER_DID", "")
TRIGGER_KEYWORDS = ["!t", "!c", "!s", "!r"]

def _sanitize(value: str, max_len: int = 200) -> str:
    if not value: return "(empty)"
    v = value.strip()
    for secret in [BOT_PASSWORD, os.getenv("PAT", ""), os.getenv("TAVILY_API_KEY", "")]:
        if secret and secret in v:
            v = v.replace(secret, "***REDACTED***")
    return v[:max_len] + ("..." if len(value) > max_len else "")

async def process_item(client, item, llm):
    uri, user_text = item["uri"], item["text"]
    do_search, search_type = item.get("has_search", False), item.get("search_type", "tavily")
    
    logger.debug(f"[DEBUG] process_item START | uri={uri} | user_text={_sanitize(user_text)} | do_search={do_search} | search_type={search_type}")
    
    logger.debug(f"[DEBUG] Calling bsky.get_record({uri})")
    rec = await bsky.get_record(client, uri)
    logger.debug(f"[DEBUG] bsky.get_record RESPONSE | status={'OK' if rec else 'FAILED'} | rec_keys={list(rec.keys()) if rec else None}")
    
    if not rec:
        logger.warning("Record not found, skipping.")
        return
    
    reply_info = rec["value"].get("reply", {})
    root_uri = reply_info.get("root", {}).get("uri", uri)
    root_cid = reply_info.get("root", {}).get("cid", "")
    parent_cid = rec.get("cid", "")
    thread_id = root_uri
    
    logger.debug(f"[DEBUG] Extracted reply_info | root_uri={root_uri} | root_cid={root_cid[:10] if root_cid else '(empty)'}... | parent_cid={parent_cid[:10] if parent_cid else '(empty)'}... | thread_id={thread_id}")
    
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    logger.debug(f"[DEBUG] Calling bsky.get_thread_raw(root_uri={root_uri})")
    raw_thread = await bsky.get_thread_raw(client, root_uri, token)
    logger.debug(f"[DEBUG] bsky.get_thread_raw RESPONSE | status={'OK' if raw_thread else 'FAILED'} | thread_keys={list(raw_thread.keys()) if raw_thread else None}")
    
    posts = await parser.parse_thread(raw_thread, token, client) if raw_thread else []
    logger.debug(f"[DEBUG] parser.parse_thread RESULT | posts_count={len(posts)}")
    
    root_post = next((p for p in posts if p.get("is_root")), {})
    logger.debug(f"[DEBUG] Found root_post | is_root={bool(root_post)} | handle={root_post.get('handle')} | text_preview={_sanitize(root_post.get('text', ''))}")
    
    if not root_post and root_uri != uri:
        logger.debug(f"[DEBUG] Fallback: fetching root via get_record({root_uri})")
        root_rec = await bsky.get_record(client, root_uri)
        logger.debug(f"[DEBUG] Fallback get_record RESPONSE | status={'OK' if root_rec else 'FAILED'}")
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
            logger.info(f"[FALLBACK] Loaded ROOT from get_record | handle={root_post['handle']}")
    
    relevant_posts = []
    for p in posts:
        if p.get("is_root"):
            continue
        if p.get("author", {}).get("did") == OWNER_DID:
            text = p.get("text", "")
            if any(trigger in text.lower() for trigger in TRIGGER_KEYWORDS):
                relevant_posts.append(p)
                logger.debug(f"[DEBUG] Added relevant owner post | text={_sanitize(text)}")
    
    recent_posts = relevant_posts[:10]
    bsky_context_str = "\n".join([
        f"@{p.get('handle', 'unknown')}: {p.get('text', '')}"
        for p in ([root_post] if root_post else []) + recent_posts
    ])
    logger.info(f"[2] Bluesky Context Fetched:\n{bsky_context_str if bsky_context_str else '(Empty - API error)'}")
    logger.debug(f"[DEBUG] Context summary | root_posts=1 | recent_posts={len(recent_posts)} | total_chars={len(bsky_context_str)}")
    
    logger.debug(f"[DEBUG] Calling state.load_context({thread_id})")
    memory = state.load_context(thread_id)
    logger.debug(f"[DEBUG] state.load_context RESULT | memory_len={len(memory)} | memory_preview={_sanitize(memory)}")
    
    search_results = ""
    if do_search:
        root_text = root_post.get("text", "") if root_post else ""
        logger.debug(f"[DEBUG] Calling generator.extract_search_params | user_text={_sanitize(user_text)} | root_text={_sanitize(root_text)}")
        search_params = generator.extract_search_params(llm, user_text, root_text)
        logger.debug(f"[DEBUG] extract_search_params RESULT | params={search_params}")
        
        provider = search.SEARCH_PROVIDERS.get(search_type)
        if provider:
            func = provider["func"]
            supported = provider.get("supports", [])
            kwargs = {k: v for k, v in search_params.items() if k in supported}
            kwargs.pop('query', None)
            logger.info(f"[3] Search Query Generated: {search_params['query']} | Provider: {search_type} | kwargs={kwargs}")
            
            logger.debug(f"[DEBUG] Calling {search_type}_search(query={_sanitize(search_params['query'])})")
            search_results = await func(search_params["query"], **kwargs)
            logger.debug(f"[DEBUG] Search API RESPONSE | result_len={len(str(search_results))} | result_preview={_sanitize(str(search_results)[:500])}")
            
            if not search.is_search_result_valid(search_results, search_type):
                search_results = ""
                logger.warning("Search result invalid, cleared.")
            else:
                logger.info(f"[4] Search Response Received ({len(search_results)} chars)")
    
    logger.debug(f"[DEBUG] Calling state.merge_contexts | root_post={bool(root_post)} | recent_posts={len(recent_posts)} | memory={bool(memory)} | search_results={bool(search_results)}")
    full_context = state.merge_contexts(root_post, recent_posts, memory, search_results, user_text)
    logger.info(f"[5] Final Context Assembled:\n{full_context}")
    logger.debug(f"[DEBUG] Final context stats | total_chars={len(full_context)} | sections={full_context.count('[')}")
    
    logger.debug(f"[DEBUG] Calling generator.get_answer | memory_len={len(memory)} | context_len={len(full_context)} | user_text_len={len(user_text)} | do_search={do_search}")
    reply = generator.get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type)
    logger.debug(f"[DEBUG] generator.get_answer RAW RESPONSE | reply_len={len(reply)} | reply_preview={_sanitize(reply)}")
    
    reply = generator.add_signature(reply, search_type if do_search else None)
    logger.info(f"[7] Final Reply to Bluesky:\n{reply}")
    logger.debug(f"[DEBUG] Final reply stats | total_chars={len(reply)} | signature={'present' if 'Qwen' in reply else 'missing'}")
    
    logger.debug(f"[DEBUG] Calling bsky.post_reply | bot_did={BOT_DID} | reply_len={len(reply)} | root_uri={root_uri} | root_cid={root_cid[:10] if root_cid else '(empty)'}... | parent_uri={uri} | parent_cid={parent_cid[:10] if parent_cid else '(empty)'}...")
    try:
        result = await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        logger.info("Reply posted successfully.")
        logger.debug(f"[DEBUG] bsky.post_reply RESPONSE | result_keys={list(result.keys()) if result else None} | result={result}")
    except Exception as e:
        logger.error(f"Failed to post reply: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return
    
    new_summary = generator.update_summary(llm, memory, user_text, reply)
    logger.info(f"[6] Saving Context to Secret:\n{new_summary}")
    logger.debug(f"[DEBUG] Calling state.save_context | thread_id={thread_id} | summary_len={len(new_summary)}")
    state.save_context(thread_id, new_summary)
    logger.debug(f"[DEBUG] process_item END | status=SUCCESS")

async def main():
    logger.debug("[DEBUG] main() START")
    async with bsky.get_client() as client:
        logger.debug(f"[DEBUG] Created httpx client | base_url={bsky.BASE_URL} | timeout=30")
        try:
            logger.debug(f"[DEBUG] Calling bsky.login | handle={BOT_HANDLE} | password=***REDACTED***")
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
            logger.info("Authenticated with Bluesky API")
            logger.debug(f"[DEBUG] Client headers after login | Authorization={'Bearer ***' if 'Authorization' in client.headers else 'MISSING'}")
        except Exception as e:
            logger.error(f"Auth failed: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return
        
        logger.debug("[DEBUG] Calling news.check_mini_timer()")
        digest_due, _ = news.check_mini_timer()
        logger.debug(f"[DEBUG] news.check_mini_timer RESULT | digest_due={digest_due}")
        
        has_notifications = os.path.exists("work_data.json")
        logger.debug(f"[DEBUG] Checking work_data.json | exists={has_notifications}")
        
        llm = None
        if digest_due or has_notifications:
            logger.debug("[DEBUG] Loading LLM model")
            try:
                llm = generator.get_model()
                logger.info("Model loaded successfully.")
                logger.debug(f"[DEBUG] LLM config | model_path={config.MODEL_PATH} | n_ctx={config.MODEL_N_CTX} | n_threads={config.MODEL_N_THREADS}")
            except Exception as e:
                logger.error(f"Model load failed, skipping heavy tasks: {e}")
                import traceback
                logger.debug(traceback.format_exc())
                return
        
        if llm:
            logger.debug("[DEBUG] Checking active digest URI")
            active_uri = state.load_active_digest_uri()
            logger.debug(f"[DEBUG] state.load_active_digest_uri RESULT | active_uri={active_uri}")
            if active_uri and active_uri not in ("{}", "null", ""):
                try:
                    logger.debug(f"[DEBUG] Fetching active digest record | uri={active_uri}")
                    active_rec = await bsky.get_record(client, active_uri)
                    active_text = active_rec["value"].get("text", "") if active_rec else ""
                    logger.debug(f"[DEBUG] Active digest text preview | len={len(active_text)} | preview={_sanitize(active_text[:100])}")
                    logger.info("Processing active digest engagement...")
                    await engagement.process_digest_engagement(client, llm, active_uri, active_text)
                    logger.debug("[DEBUG] engagement.process_digest_engagement COMPLETED")
                except Exception as e:
                    logger.error(f"Failed to process active digest engagement: {e}")
                    import traceback
                    logger.debug(traceback.format_exc())
            
            if digest_due and llm:
                logger.info("Posting daily digest...")
                logger.debug("[DEBUG] Calling news.post_if_due")
                await news.post_if_due(client, llm)
                logger.debug("[DEBUG] news.post_if_due COMPLETED")
            
            if has_notifications and llm:
                logger.info("Processing notifications...")
                logger.debug("[DEBUG] Loading work_data.json")
                with open("work_data.json", "r") as f:
                    work_data = json.load(f)
                logger.debug(f"[DEBUG] work_data.json loaded | items_count={len(work_data.get('items', []))}")
                if work_data.get("items"):
                    for idx, item in enumerate(work_data["items"], 1):
                        logger.debug(f"[DEBUG] Processing item {idx}/{len(work_data['items'])} | uri={item.get('uri')}")
                        await process_item(client, item, llm)
                        logger.debug(f"[DEBUG] Item {idx} COMPLETED")
                        await asyncio.sleep(1)
        
        logger.debug("[DEBUG] main() END")

if __name__ == "__main__":
    logger.info("=== BOT STARTED ===")
    logger.debug(f"[DEBUG] Environment | BOT_HANDLE={BOT_HANDLE} | BOT_DID={BOT_DID} | OWNER_DID={OWNER_DID}")
    logger.debug(f"[DEBUG] Config | MODEL_PATH={config.MODEL_PATH} | MODEL_N_CTX={config.MODEL_N_CTX} | CONTEXT_SLOT_COUNT={config.CONTEXT_SECRET_COUNT}")
    asyncio.run(main())
    logger.info("=== BOT FINISHED ===")
