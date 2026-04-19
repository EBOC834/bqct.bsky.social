import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os
import asyncio
from core.config import BOT_HANDLE, BOT_PASSWORD, BOT_DID, OWNER_DID
from core.state import get_queue, clear_queue, load_context, save_context
from core.bsky import get_client, login, fetch_thread_chain, post_reply
from core.parser import extract_clean_url_content, extract_embed_full
from core.search import tavily_search, chainbase_search, format_search_result
from core.generator import get_model, get_answer, extract_search_params, update_summary, get_signature
from core.utils import clean_artifacts, extract_urls

async def process_item(client, item, llm):
    uri, user_text = item["uri"], item["text"]
    do_search, search_type = item.get("has_search", False), item.get("search_type", "tavily")
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    chain_data = await fetch_thread_chain(client, uri, token)
    if not chain_data:
        return
    root_uri = chain_data["root_uri"]
    root_cid = chain_data["root_cid"]
    parent_cid = chain_data["parent_cid"]
    chain = chain_data["chain"]
    relevant_posts = []
    link_cache = {}
    root_post = None
    for idx, post in enumerate(chain):
        rec = post.get("record", {})
        author = post.get("author", {})
        did = author.get("did", "")
        handle = author.get("handle", "")
        text = rec.get("text", "")
        embed = rec.get("embed")
        embed_text, alts = extract_embed_full(embed) if embed else ("", [])
        link_hints = []
        urls = extract_urls(text)
        for url in urls:
            if url not in link_cache:
                clean = await extract_clean_url_content(url)
                link_cache[url] = clean
                if clean:
                    link_hints.append(f"[Page content: {clean[:400]}]")
        post_data = {"uri": post.get("uri"), "handle": handle, "did": did, "text": text, "embed": embed_text, "link_hints": link_hints, "alts": alts, "is_root": (idx == 0)}
        if idx == 0:
            root_post = post_data
        elif did == OWNER_DID:
            relevant_posts.append(post_data)
    thread_id = root_uri
    memory = load_context(thread_id)
    search_results = ""
    if do_search:
        query_ctx = " ".join([f"@{p['handle']}: {p['text']}" for p in relevant_posts[-3:]])
        params = extract_search_params(llm, query_ctx, user_text)
        if search_type == "chainbase":
            res = await chainbase_search(params.get("query", ""))
        else:
            res = await tavily_search(params.get("query", ""), params.get("time_range"), params.get("topic"))
        if res:
            search_results = format_search_result(res, search_type)
    context_parts = []
    for p in reversed(relevant_posts[-5:]):
        txt = p.get("text", "")
        if p.get("link_hints"):
            txt += "\n" + "\n".join(p["link_hints"])
        if p.get("alts"):
            txt += "\n" + "\n".join(p["alts"])
        context_parts.append(f"@{p.get('handle', 'unknown')}: {txt}")
    if user_text:
        context_parts.append(f"[User Question]:\n{user_text}")
    if memory:
        context_parts.append(f"[Memory]:\n{memory}")
    if search_results:
        context_parts.append(f"[Search Results]:\n{search_results}")
    if root_post:
        context_parts.append(f"[ROOT] @{root_post.get('handle', 'unknown')}: {root_post.get('text', '')}")
    full_context = "\n".join(context_parts)
    sig = get_signature(search_type if do_search else None)
    max_chars = 300 - len(sig) - 15
    reply = get_answer(llm, memory, full_context, search_results, user_text, do_search, search_type, max_chars)
    final = f"{reply}{sig}"
    if len(final) > 300:
        final = final[:300].rsplit(' ', 1)[0]
    await post_reply(client, BOT_DID, final, root_uri, root_cid, uri, parent_cid)
    new_summary = update_summary(llm, memory, user_text, reply)
    save_context(thread_id, new_summary)

async def main():
    items = get_queue()
    if not items:
        return
    async with get_client() as client:
        await login(client, BOT_HANDLE, BOT_PASSWORD)
        llm = get_model()
        for item in items:
            await process_item(client, item, llm)
            await asyncio.sleep(1)
        clear_queue()

if __name__ == "__main__":
    asyncio.run(main())
