import os
import json
import asyncio
import logging
import bsky
import news
import state
import search
import generator
import engagement
from config import BOT_HANDLE, BOT_PASSWORD, BOT_DID

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

async def process_item(client, item, llm):
    uri = item["uri"]
    user_text = item["text"]
    search_type = item.get("search_type")
    thread = await bsky.fetch_thread_context(client, uri)
    if not thread: return
    memory = state.load_context(thread["root_uri"])
    search_res = await search.execute_if_needed(llm, item, thread["root_text"])
    prompt = state.build_prompt(thread, memory, search_res, user_text)
    reply = generator.get_answer(llm, memory, prompt, search_res, user_text, item.get("has_search", False), search_type)
    reply = generator.add_signature(reply, search_type if item.get("has_search", False) else None)
    await bsky.post_reply(client, BOT_DID, reply, thread["root_uri"], "", thread["parent_uri"], thread["parent_cid"])
    new_memory = generator.update_summary(llm, memory, user_text, reply)
    state.save_context(thread["root_uri"], new_memory)

async def main():
    async with bsky.get_client() as client:
        if not await bsky.login(client, BOT_HANDLE, BOT_PASSWORD):
            return
        digest_due, _ = news.check_mini_timer()
        has_notifications = os.path.exists("work_data.json")
        llm = None
        if digest_due or has_notifications:
            try: llm = generator.get_model()
            except Exception as e:
                logger.error(f"Model load failed: {e}")
                return
        if llm:
            active_uri = state.load_active_digest_uri()
            if active_uri and active_uri not in ("{}", "null", ""):
                try:
                    active_rec = await bsky.get_record(client, active_uri)
                    active_text = active_rec["value"].get("text", "") if active_rec else ""
                    await engagement.process_digest_engagement(client, llm, active_uri, active_text)
                except Exception as e: logger.error(f"Digest engagement failed: {e}")
            if digest_due:
                await news.post_if_due(client, llm)
            if has_notifications:
                with open("work_data.json", "r") as f: work_data = json.load(f)
                for item in work_data.get("items", []):
                    await process_item(client, item, llm)
                    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
