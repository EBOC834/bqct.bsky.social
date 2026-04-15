import os
import json
import asyncio
from datetime import datetime, timezone, timedelta

import config
import context
import search
import generator
import bsky
import parser
import prompts
import news

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

    root_rec = await bsky.get_record(client, root_uri)
    root_post = parser.parse_bluesky_post(root_rec) if root_rec else {}

    recent_posts = []
    try:
        parts = root_uri.split("/")
        if len(parts) >= 5:
            did, collection, rkey = parts[2], parts[3], parts[4]
            r = await client.get("/xrpc/com.atproto.feed.getPostThread", params={"uri": root_uri, "depth": 20}, timeout=30)
            if r.status_code == 200:
                thread_data = r.json()
                all_posts = parser.parse_bluesky_thread(thread_data, root_uri)
                recent_posts = [p for p in all_posts if not p.get("is_root")][:10]
    except:
        pass

    memory = context.load_context(thread_id)
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

    full_context = context.merge_contexts(root_post, recent_posts, memory, search_results)
    reply = generator.get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type)

    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
    except:
        return

    if search_results or not do_search:
        new_summary = generator.update_summary(llm, memory, user_text, reply)
        context.save_context(thread_id, new_summary)

async def main():
    if not os.path.exists("work_data.json"):
        return
    with open("work_data.json", "r") as f:
        work_data = json.load(f)
    if not work_data.get("items"):
        return

    llm = generator.get_model()
    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
            await news.post_daily_digest(client)
            for item in work_data["items"]:
                await process_item(client, item, llm)
                await asyncio.sleep(1)
        except Exception as e:
            print(f"Fatal error: {e}", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
