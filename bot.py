import os
import json
import asyncio

import config
import memory
import search
import generator
import bsky

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

    # 1. Получаем готовый контекст (не знаем, как он собран)
    fresh_context = await bsky.get_context_string(client, root_uri, BOT_HANDLE)
    persisted_context = memory.load_context(thread_id)

    # 2. Определяем параметры поиска (не знаем, как модель их извлекла)
    search_results = ""
    search_valid = False
    if do_search:
        search_params = generator.extract_search_params(llm, user_text)
        print(f"Searching ({search_type}) for: {search_params['query']} | time:{search_params['time_range']} | topic:{search_params['topic']}", flush=True)
        
        provider = search.SEARCH_PROVIDERS.get(search_type)
        if provider:
            func = provider["func"]
            supported = provider.get("supports", [])
            kwargs = {k: v for k, v in search_params.items() if k in supported}
            
            # Убираем query из kwargs, так как он идет первым аргументом
            kwargs.pop('query', None)
            
            search_results = await func(search_params["query"], **kwargs)
            search_valid = search.is_search_result_valid(search_results, search_type)

    # 3. Генерируем ответ (не знаем, какой промпт использовался)
    reply = generator.get_answer(
        llm,
        context_str=persisted_context,
        user_text=user_text,
        search_results=search_results if search_valid else "",
        do_search=do_search,
        search_type=search_type
    )
    print(f"Reply: {reply}", flush=True)

    try:
        await bsky.post_reply(client, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        print("Posted!", flush=True)
    except Exception as e:
        print(f"Post failed: {e}", flush=True)
        return

    # 4. Обновляем память (не знаем, как формируется саммари)
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
