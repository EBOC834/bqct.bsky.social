import os
import sys
import json
import asyncio
import httpx
from llama_cpp import Llama

import prompts
import bsky

MODEL_PATH = "models/Qwen2.5-14B-Q4_K_M.gguf"
MODEL_N_CTX = 2048
MODEL_N_THREADS = 2
TEMPERATURE = 0.7
MAX_TOKENS = 512
RESPONSE_MAX_CHARS = 300

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
BOT_DID = os.getenv("BOT_DID")

async def tavily_search(query):
    if not TAVILY_API_KEY:
        return "Tavily API Key missing."
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": query, "search_depth": "basic", "include_answer": True, "max_results": 3},
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                summary = f"AI Answer: {data.get('answer', '')}\n" if data.get("answer") else ""
                for res in data.get("results", []):
                    summary += f"- {res.get('title', '')}: {res.get('content', '')[:150]}...\n"
                return summary[:1000]
    except Exception as e:
        return f"Error: {e}"

async def chainbase_search(query):
    try:
        async with httpx.AsyncClient() as client:
            url = "https://api.chainbase.com/tops/v1/tool/search-narrative-candidates"
            r = await client.get(
                url,
                headers={"x-api-key": "demo"},
                params={"keyword": query, "language": "en", "limit": 3},
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                summary = ""
                for item in data.get("items", [])[:3]:
                    keyword = item.get("keyword", "")
                    text = item.get("summary", "")[:150]
                    summary += f"- {keyword}: {text}...\n"
                return summary[:1000] if summary else "No specific trends found."
    except Exception as e:
        return f"Error: {e}"

def ask(llm, system_prompt, user_prompt):
    prompt = f"  system\n{system_prompt}\n  user\n{user_prompt}\n  assistant\n"
    out = llm(
        prompt,
        max_tokens=MAX_TOKENS,
        temperature=TEMPERATURE,
        stop=["  user", "  system", "  assistant"],
        echo=False
    )
    raw_text = out["choices"][0]["text"].strip()
    return raw_text[:RESPONSE_MAX_CHARS]

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
    
    thread_posts = await bsky.get_thread_context(client, token, root_uri)
    context_str = ""
    relevant_posts = []
    if len(thread_posts) > 5:
        relevant_posts = [thread_posts[0]] + thread_posts[-4:]
    else:
        relevant_posts = thread_posts

    for post in relevant_posts:
        marker = " [BOT]" if post.get("handle") == BOT_HANDLE else ""
        context_str += f"@{post['handle']}{marker}: {post['text']}\n"

    print(f"[LOG] CONTEXT:\n{context_str[:100]}...", flush=True)

    search_results = ""
    if do_search:
        query = user_text.replace("!t", "").replace("!c", "").strip()
        print(f"Searching ({search_type}) for: {query}", flush=True)
        if search_type == "chainbase":
            search_results = await chainbase_search(query)
        else:
            search_results = await tavily_search(query)
        print(f"[LOG] SEARCH RESULTS:\n{search_results[:100]}...", flush=True)

    personality = prompts.ANSWER_PROMPTS.get(1, "Answer concisely.")
    
    final_prompt = (
        f"Context:\n{context_str}\n"
        f"Search Results:\n{search_results}\n"
        f"User Question:\n{user_text}\n"
        f"Answer:"
    )

    try:
        reply = ask(llm, personality, final_prompt)
        if not reply or len(reply.strip()) < 2:
            reply = "..."
        print(f"Reply: {reply}", flush=True)
        parent_cid = rec.get("cid")
        root_cid = reply_info.get("root", {}).get("cid", "")
        await bsky.post_reply(client, token, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        print("Posted!", flush=True)
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

    print(f"Loading {MODEL_PATH}...", flush=True)
    if not os.path.exists(MODEL_PATH):
        print(f"Model not found at {MODEL_PATH}!", flush=True)
        return

    llm = Llama(
        model_path=MODEL_PATH,
        n_ctx=MODEL_N_CTX,
        n_threads=MODEL_N_THREADS,
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
