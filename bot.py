import os
import json
import asyncio

import config
import memory
import search
import generator
import bsky
import prompts

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
BOT_DID = os.getenv("BOT_DID")

async def process_item(client, item, llm):
    uri, user_text = item["uri"], item["text"]
    do_search, search_type = item.get("has_search", False), item.get("search_type", "tavily")

    print(f"[DEBUG] work_data flags: has_search={do_search}, search_type={search_type}", flush=True)
    print(f"[DEBUG] user_text: {user_text}", flush=True)

    rec = await bsky.get_record(client, uri)
    if not rec:
        print("Warning: Record not found, skipping.", flush=True)
        return

    reply_info = rec["value"].get("reply", {})
    root_uri = reply_info.get("root", {}).get("uri", uri)
    root_cid = reply_info.get("root", {}).get("cid", "")
    parent_cid = rec.get("cid", "")
    thread_id = root_uri

    fresh_context = await bsky.get_context_string(client, root_uri, BOT_HANDLE)
    persisted_context = memory.load_context(thread_id)

    search_results = ""
    search_valid = False
    if do_search:
        print("[DEBUG] === CONTEXT FOR QUERY EXTRACTION ===", flush=True)
        print(f"User Message: {user_text}", flush=True)
        print(f"Thread Summary: {persisted_context[:150] if persisted_context else 'None'}", flush=True)
        print(f"Recent Posts: {fresh_context[:150] if fresh_context else 'None'}", flush=True)
        print("[DEBUG] =====================================", flush=True)

        search_params = generator.extract_search_params(llm, user_text)
        print(f"[DEBUG] Extracted Params: {search_params}", flush=True)
        
        provider = search.SEARCH_PROVIDERS.get(search_type)
        if provider:
            func = provider["func"]
            supported = provider.get("supports", [])
            kwargs = {k: v for k, v in search_params.items() if k in supported}
            kwargs.pop('query', None)
            print(f"[DEBUG] Search request: query='{search_params['query']}', kwargs={kwargs}", flush=True)
            search_results = await func(search_params["query"], **kwargs)
            search_valid = search.is_search_result_valid(search_results, search_type)
            print(f"[DEBUG] Search Valid: {search_valid} | Results Length: {len(search_results)}", flush=True)

    print("[DEBUG] === CONTEXT FOR ANSWER GENERATION ===", flush=True)
    full_context = ""
    if persisted_context:
        full_context += f"Thread Summary:\n{persisted_context}\n\n"
    if fresh_context:
        full_context += f"Recent Context:\n{fresh_context}\n\n"
    if search_results and search_valid:
        full_context += f"Search Results:\n{search_results}\n\n"
    
    print(f"Full Context Block:\n{full_context}", flush=True)
    print(f"User Question: {user_text}", flush=True)
    
    debug_prompt = (
        f"  system\n{prompts.ANSWER_SYSTEM}\n"
        f"  user\n{full_context}User Question:\n{user_text}\n"
        f"  assistant\n"
    )
    print(f"[DEBUG] FULL PROMPT TO MODEL:\n{debug_prompt}", flush=True)
    print("[DEBUG] =========================================", flush=True)

    reply = generator.get_answer(
        llm,
        memory_context=persisted_context,
        fresh_context=fresh_context,
        search_results=search_results if search_valid else "",
        user_text=user_text,
        do_search=do_search,
        search_type=search_type
    )
    print(f"Reply: {reply}", flush=True)

    print(f"[DEBUG] Post params: root_uri={root_uri[:20]}..., root_cid={root_cid[:10] if root_cid else 'EMPTY'}, parent_uri={uri[:20]}..., parent_cid={parent_cid[:10] if parent_cid else 'EMPTY'}", flush=True)
    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        print("Posted!", flush=True)
    except Exception as e:
        print(f"Post failed: {e}", flush=True)
        return

    if search_valid or not do_search:
        try:
            new_summary = generator.update_summary(llm, persisted_context, user_text, reply)
            memory.save_context(thread_id, new_summary)
            print("Context updated.", flush=True)
        except Exception as e:
            print(f"Memory update failed: {e}", flush=True)

async def main():
    if not os.path.exists("work_data.json"):
        print("No work_data.json", flush=True)
        return
    with open("work_data.json", "r") as f:
        work_data = json.load(f)
    if not work_data.get("items"):
        print("Empty queue.", flush=True)
        return

    llm = generator.get_model()
    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
            for item in work_data["items"]:
                await process_item(client, item, llm)
                await asyncio.sleep(1)
        except Exception as e:
            print(f"Fatal error: {e}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
