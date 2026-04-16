import os
import sys
from datetime import datetime, timezone

raw = os.getenv("LAST_NEWS", "").strip()
has_notifications = os.path.exists("work_data.json")

if not has_notifications:
    if not raw or raw == "{}" or raw == "null":
        pass
    else:
        try:
            last_ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            diff = datetime.now(timezone.utc) - last_ts
            if diff.total_seconds() < 6 * 3600:
                print("[BOT] No notifications and 6h not passed. Exiting.")
                sys.exit(0)
        except:
            pass

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
    uri, user_text = item["uri"], item["text"]
    do_search, search_type = item.get("has_search", False), item.get("search_type", "tavily")
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
    search_results = ""
    if do_search:
        search_params = generator.extract_search_params(llm, user_text)
        provider = search.SEARCH_PROVIDERS.get(search_type)
        if provider:
            func = provider["func"]
            supported = provider.get("supports", [])
            kwargs = {k: v for k, v in search_params.items() if k in supported}
            kwargs.pop('query', None)
            search_results = await func(search_params["query"], **kwargs)
            if not search.is_search_result_valid(search_results, search_type):
                search_results = ""
    full_context = context_module.merge_contexts(root_post, recent_posts, memory, search_results)
    reply = generator.get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type)
    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
    except:
        return
    new_summary = generator.update_summary(llm, memory, user_text, reply)
    context_module.save_context(thread_id, new_summary)

async def main():
    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
        except Exception as e:
            print(f"[BOT] Auth failed: {e}")
            return

        digest_due, _ = news.should_post()
        has_notifications = os.path.exists("work_data.json")

        if digest_due:
            print("[BOT] Digest is due. Posting news...")
            await news.post_if_due(client)

        if has_notifications:
            print("[BOT] Notifications found. Loading model...")
            llm = generator.get_model()
            with open("work_data.json", "r") as f:
                work_data = json.load(f)
            if work_data.get("items"):
                for item in work_data["items"]:
                    await process_item(client, item, llm)
                    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
