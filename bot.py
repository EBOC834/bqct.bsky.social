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
    print(f"Processing: {user_text[:30]}...", flush=True)

    rec = await bsky.get_record(client, uri)
    if not rec:
        print("Warning: Record not found, skipping.", flush=True)
        return

    reply_info = rec["value"].get("reply", {})
    root_uri = reply_info.get("root", {}).get("uri", uri)
    root_cid = reply_info.get("root", {}).get("cid", "")
    parent_cid = rec.get("cid", "")
    thread_id = root_uri

    thread_posts = await bsky.get_thread_context(client, root_uri)
    selected = bsky.filter_and_select(thread_posts, BOT_HANDLE)
    fresh_context = bsky.format_context(selected, BOT_HANDLE)
    persisted_context = memory.load_context(thread_id)

    search_results, search_valid = "", False
    if do_search:
        search_params = generator.extract_search_params(llm, user_text)
        query = search_params["query"]
        time_range = search_params["time_range"]
        topic = search_params["topic"]
        print(f"Searching ({search_type}) for: {query} | time:{time_range or 'any'} | topic:{topic or 'any'}", flush=True)
        func = search.SEARCH_PROVIDERS.get(search_type)
        if func:
            if search_type == "tavily":
                search_results = await func(query, time_range=time_range, topic=topic)
            else:
                search_results = await func(query)
            search_valid = search.is_search_result_valid(search_results, search_type)

    full_context = ""
    if persisted_context:
        full_context += f"Thread Summary:\n{persisted_context}\n\n"
    full_context += f"Recent Context:\n{fresh_context}\n"
    if search_valid:
        full_context += f"Search Results:\n{search_results}\n"

    final_prompt = (
        f"  system\n{prompts.ANSWER_SYSTEM}\n"
        f"  user\n{full_context}\nUser Question:\n{user_text}\n"
        f"  assistant\n"
    )
    reply = generator.generate(llm, final_prompt, stop=["  user", "  system", "  assistant"])
    reply = generator.format_reply(reply, do_search, search_type)
    print(f"Reply: {reply}", flush=True)

    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        print("Posted!", flush=True)
    except Exception as e:
        print(f"Post failed: {e}", flush=True)
        return

    if search_valid or not do_search:
        try:
            new_summary = generator.generate_summary(llm, persisted_context, f"User: {user_text}\nBot: {reply}")
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
