import os
import json
import asyncio

import config
import context as context_module
import search
import generator
import bsky
import news
import parser

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
BOT_DID = os.getenv("BOT_DID")

async def process_item(client, item, llm):
    print(f"[LOG] Input item: {item['uri']}")
    print(f"[LOG] Raw query: {item['text']}")

    clean_text, has_search, search_type = parser.parse_operators(item["text"])
    print(f"[LOG] Cleaned query: {clean_text}")
    print(f"[LOG] Search config: has_search={has_search}, type={search_type}")

    uri, user_text = item["uri"], clean_text
    do_search = has_search

    rec = await bsky.get_record(client, uri)
    if not rec:
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
    recent_posts = [p for p in posts if not p.get("is_root")][:10]
    memory = context_module.load_context(thread_id)

    print(f"[LOG] Thread memory context: {memory}")

    search_results = ""
    if do_search:
        search_params = generator.extract_search_params(llm, user_text)
        print(f"[LOG] Generated search query: {search_params.get('query', '')}")
        
        provider = search.SEARCH_PROVIDERS.get(search_type)
        if provider:
            func = provider["func"]
            supported = provider.get("supports", [])
            kwargs = {k: v for k, v in search_params.items() if k in supported}
            kwargs.pop('query', None)
            
            search_results = await func(search_params["query"], **kwargs)
            if not search.is_search_result_valid(search_results, search_type):
                search_results = ""
            
            print(f"[LOG] Search results received: {search_results[:300]}...")

    full_context = context_module.merge_contexts(root_post, recent_posts, memory, search_results)
    print(f"[LOG] Full context for model: {full_context[:500]}...")

    reply = generator.get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type)
    print(f"[LOG] Final reply generated: {reply}")

    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
    except:
        return

    new_summary = generator.update_summary(llm, memory, user_text, reply)
    context_module.save_context(thread_id, new_summary)
    print(f"[LOG] Saved context to secret: {new_summary}")

async def main():
    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
        except Exception as e:
            print(f"[BOT] Auth failed: {e}")
            return

        digest_due, _ = news.should_post()
        has_notifications = os.path.exists("work_data.json")

        if digest_due or has_notifications:
            llm = generator.get_model()
        else:
            llm = None

        if digest_due and llm:
            print("[BOT] Posting digest...")
            await news.post_if_due(client)

        if has_notifications and llm:
            print("[BOT] Processing notifications...")
            with open("work_data.json", "r") as f:
                work_data = json.load(f)
            if work_data.get("items"):
                for item in work_data["items"]:
                    await process_item(client, item, llm)
                    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
