import os
import json
import asyncio
import httpx
from llama_cpp import Llama

import prompts
import bsky
import config
import context
from sources import SEARCH_PROVIDERS, SOURCE_SUFFIXES

BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
BOT_DID = os.getenv("BOT_DID")

def ask(llm, system_prompt, user_prompt):
    prompt = f"  system\n{system_prompt}\n  user\n{user_prompt}\n  assistant\n"
    out = llm(
        prompt,
        max_tokens=config.MAX_TOKENS,
        temperature=config.TEMPERATURE,
        stop=["  user", "  system", "  assistant"],
        echo=False
    )
    raw_text = out["choices"][0]["text"].strip()
    return raw_text[:config.RESPONSE_MAX_CHARS]

def extract_embed_text(post):
    text_parts = []
    embed = post.get("embed", {})
    if embed.get("$type") == "app.bsky.embed.images":
        for img in embed.get("images", []):
            alt = img.get("alt", "").strip()
            if alt:
                text_parts.append(f"[Image: {alt}]")
    elif embed.get("$type") == "app.bsky.embed.external":
        ext = embed.get("external", {})
        title = ext.get("title", "").strip()
        desc = ext.get("description", "").strip()
        if title:
            text_parts.append(f"[Link: {title}]")
        if desc:
            text_parts.append(f"[Desc: {desc[:100]}]")
    elif embed.get("$type") == "app.bsky.embed.record":
        rec = embed.get("record", {})
        rec_text = rec.get("text", "").strip()
        if rec_text:
            text_parts.append(f"[Quote: {rec_text[:100]}]")
    return " ".join(text_parts)

def build_context_str(thread_posts, bot_handle):
    lines = []
    for post in thread_posts:
        handle = post.get("handle", "")
        text = post.get("text", "")
        marker = " [BOT]" if handle == bot_handle else ""
        embed_text = extract_embed_text(post)
        line = f"@{handle}{marker}: {text}"
        if embed_text:
            line += f" {embed_text}"
        lines.append(line)
    return "\n".join(lines)

def filter_posts(posts, min_len=5):
    result = []
    seen = set()
    for p in posts:
        text = p.get("text", "").strip()
        if len(text) < min_len:
            continue
        if text in seen:
            continue
        seen.add(text)
        result.append(p)
    return result

def select_relevant_posts(posts, limit=8):
    if len(posts) <= limit:
        return posts
    root = posts[0]
    recent = posts[-(limit-1):]
    return [root] + recent

async def process_item(client, token, item, llm):
    uri = item["uri"]
    user_text = item["text"]
    do_search = item.get("has_search", False)
    search_type = item.get("search_type", "tavily")

    print(f"Processing: {user_text[:30]}...", flush=True)

    rec = await bsky.get_record(client, token, uri)
    if not rec:
        return

    reply_info = rec["value"].get("reply", {})
    root_uri = reply_info.get("root", {}).get("uri", uri)
    thread_id = root_uri

    thread_posts = await bsky.get_thread_context(client, token, root_uri)
    filtered = filter_posts(thread_posts)
    selected = select_relevant_posts(filtered, limit=10)
    fresh_context = build_context_str(selected, BOT_HANDLE)

    persisted_context = context.load_context(thread_id)

    search_results = ""
    if do_search:
        query = user_text.replace("!t", "").replace("!c", "").replace("!w", "").strip()
        print(f"Searching ({search_type}) for: {query}", flush=True)
        search_func = SEARCH_PROVIDERS.get(search_type)
        if search_func:
            search_results = await search_func(query)
        else:
            search_results = f"Unknown search type: {search_type}"
        print(f"[LOG] SEARCH RESULTS:\n{search_results[:100]}...", flush=True)

    personality = prompts.ANSWER_PROMPTS.get(1, "Answer concisely.")
    
    full_context = ""
    if persisted_context:
        full_context += f"Thread Summary:\n{persisted_context}\n\n"
    full_context += f"Recent Context:\n{fresh_context}\n"
    if search_results:
        full_context += f"Search Results:\n{search_results}\n"
    
    final_prompt = (
        f"{full_context}"
        f"User Question:\n{user_text}\n"
        f"Answer:"
    )

    try:
        reply = ask(llm, personality, final_prompt)
        if not reply or len(reply.strip()) < 2:
            reply = "..."
        
        if do_search:
            suffix = SOURCE_SUFFIXES.get(search_type, "")
            if suffix:
                max_reply_len = config.RESPONSE_MAX_CHARS - len(suffix)
                reply = reply[:max_reply_len].rstrip() + suffix
        
        print(f"Reply: {reply}", flush=True)
        
        parent_cid = rec.get("cid")
        root_cid = reply_info.get("root", {}).get("cid", "")
        await bsky.post_reply(client, token, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        print("Posted!", flush=True)

        summary_prompt = (
            f"  system\n{prompts.SUMMARIZE_PROMPT}\n"
            f"  user\nCurrent Summary:\n{persisted_context}\n"
            f"New Interaction:\nUser: {user_text}\nBot: {reply}\n"
            f"Update summary concisely, keep only essential info. Output only the new summary.\n"
            f"  assistant\n"
        )
        new_summary_raw = llm(
            summary_prompt,
            max_tokens=256,
            temperature=0.3,
            stop=["  user", "  system", "  assistant"],
            echo=False
        )["choices"][0]["text"].strip()
        
        new_summary = new_summary_raw[:config.CONTEXT_MAX_CHARS]
        context.save_context(thread_id, new_summary)
        
    except Exception as e:
        print(f"Error generating/posting: {e}", flush=True)

async def main():
    if not os.path.exists("work_data.json"):
        print("No work data found.", flush=True)
        return

    with open("work_data.json", "r") as f:
        work_data = json.load(f)
    
    items = work_data.get("items", [])
    if not items:
        print("Empty queue.", flush=True)
        return

    print(f"Loading {config.MODEL_PATH}...", flush=True)
    if not os.path.exists(config.MODEL_PATH):
        print(f"Model not found at {config.MODEL_PATH}!", flush=True)
        return

    llm = Llama(
        model_path=config.MODEL_PATH,
        n_ctx=config.MODEL_N_CTX,
        n_threads=config.MODEL_N_THREADS,
        verbose=False,
        n_batch=512
    )
    print("Model loaded.", flush=True)

    async with bsky.get_client() as client:
        token = await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
        for item in items:
            await process_item(client, token, item, llm)
            await asyncio.sleep(1)
    print("Done.", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
