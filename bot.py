import os
import sys
import json
import asyncio
import httpx
import re
from llama_cpp import Llama
import bsky
import prompts

MODEL_PATH = "models/Qwen3-14B-Q4_K_M.gguf"
MODEL_N_CTX = 2048
MODEL_N_THREADS = 2
TEMPERATURE = 0.6
MAX_TOKENS = 150
RESPONSE_MAX_CHARS = 280
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
BOT_DID = os.getenv("BOT_DID")
BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")

def strip_reasoning(text):
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    text = re.sub(r'<think>.*', '', text, flags=re.DOTALL)
    return text.strip()

async def refine_query(llm, user_text, context_summary):
    system_prompt = "You are a search query optimizer. Create a concise, highly effective search query based on the user's question and context. Output ONLY the query."
    user_prompt = f"Context: {context_summary}\nQuestion: {user_text}\nQuery:"
    fp = f"  system\n{system_prompt}\n  user\n{user_prompt}\n  assistant\n"
    try:
        out = llm(fp, max_tokens=50, temperature=0.3, stop=["  user", "  system", "  assistant"], echo=False)
        result = out["choices"][0]["text"].strip()
        cleaned = strip_reasoning(result)
        return cleaned[:200] if cleaned else user_text[:200]
    except Exception as e:
        print(f"Refine error: {e}")
        return user_text[:200]

async def tavily_search(query):
    if not TAVILY_API_KEY:
        return ""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.tavily.com/search", json={"api_key": TAVILY_API_KEY, "query": query, "search_depth": "basic", "include_answer": True, "max_results": 3}, timeout=30)
            if r.status_code == 200:
                data = r.json()
                summary = f"AI Answer: {data.get('answer', '')}\n" if data.get("answer") else ""
                for res in data.get("results", []):
                    summary += f"- {res.get('title', '')}: {res.get('content', '')[:150]}...\n"
                return summary[:1000]
    except Exception as e:
        print(f"Tavily error: {e}")
        return ""

async def chainbase_search(query):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://api.chainbase.online/v1/trending/search", headers={"x-api-key": "demo"}, params={"q": query}, timeout=30)
            if r.status_code == 200:
                data = r.json()
                summary = ""
                for item in data.get("results", [])[:3]:
                    title = item.get("title", "") or item.get("topic", "")
                    content = item.get("description", "") or item.get("summary", "")
                    summary += f"- {title}: {content[:150]}...\n"
                return summary[:1000]
    except Exception as e:
        print(f"Chainbase error: {e}")
        return ""

def ask(llm, system_prompt, user_prompt):
    fp = f"  system\n{system_prompt}\n  user\n{user_prompt}\n  assistant\n"
    out = llm(fp, max_tokens=MAX_TOKENS, temperature=TEMPERATURE, stop=["  user", "  system", "  assistant"], echo=False)
    raw_reply = out["choices"][0]["text"].strip()
    print(f"[LOG] RAW MODEL:\n{raw_reply}", flush=True)
    cleaned_reply = strip_reasoning(raw_reply)
    print(f"[LOG] STRIPPED:\n{cleaned_reply}", flush=True)
    final_reply = " ".join(cleaned_reply.split())
    final_reply = final_reply[:RESPONSE_MAX_CHARS]
    print(f"[LOG] FINAL ({len(final_reply)} chars): {final_reply}", flush=True)
    return final_reply

async def process_item(client, token, item, llm):
    uri = item["uri"]
    user_text = item["text"]
    do_search = item["has_search"]
    print(f"Processing: {user_text[:30]}...", flush=True)
    rec = await bsky.get_record(client, token, uri)
    if not rec:
        print("Failed to get record", flush=True)
        return
    reply_info = rec["value"].get("reply", {})
    root_data = reply_info.get("root")
    if root_
        root_uri = root_data.get("uri", uri)
        root_cid = root_data.get("cid", "")
    else:
        root_uri = uri
        root_cid = rec.get("cid", "")
    parent_cid = rec.get("cid", "")
    thread_posts = await bsky.get_thread_context(client, token, root_uri)
    context_str = ""
    for post in thread_posts[-5:]:
        context_str += f"@{post['handle']}: {post['text']}\n"
    if "http" in user_text:
        urls = [u for u in user_text.split() if u.startswith("http")]
        if urls:
            meta = await bsky.extract_link_metadata(urls[0])
            if meta.get("title"):
                context_str += f"[Link Info: {meta['title']}]\n"
    print(f"[LOG] CONTEXT SEEN:\n{context_str}", flush=True)
    search_results = ""
    if do_search:
        query = user_text.replace("!t", "").replace("!c", "").strip()
        if item.get("search_type") == "chainbase":
            print("Searching Chainbase...", flush=True)
            search_results = await chainbase_search(query)
        else:
            print("Refining query with AI...", flush=True)
            query = await refine_query(llm, query, context_str)
            print(f"Refined Query: {query}", flush=True)
            search_results = await tavily_search(query)
    print(f"[LOG] SEARCH RESULTS:\n{search_results}", flush=True)
    personality = prompts.ANSWER_PROMPTS[1]
    system_prompt = f"{personality}\nStrictly under {RESPONSE_MAX_CHARS} characters total. Direct answer only. No explanations."
    user_prompt = f"Context:\n{context_str}\nSearch Results:\n{search_results}\nUser Question:\n{user_text}\nAnswer:"
    print(f"[LOG] FULL PROMPT:\n{user_prompt[:500]}...", flush=True)
    try:
        reply = ask(llm, system_prompt, user_prompt)
        if not reply:
            reply = "Got it."
            print("[LOG] Fallback used (empty generation)", flush=True)
        print(f"Reply: {reply}", flush=True)
        await bsky.post_reply(client, token, BOT_DID, reply, root_uri, root_cid, uri, parent_cid)
        print("Posted!", flush=True)
    except Exception as e:
        print(f"Generation/Post error: {e}", flush=True)

async def main():
    if not os.path.exists("work_data.json"):
        print("No work_data.json found. Exiting.", flush=True)
        sys.exit(0)
    with open("work_data.json", "r") as f:
        work_data = json.load(f)
    items = work_data.get("items", [])
    if not items:
        print("Work data empty.", flush=True)
        sys.exit(0)
    async with bsky.get_client() as client:
        token = await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
        print("Loading Qwen3-14B...", flush=True)
        llm = Llama(model_path=MODEL_PATH, n_ctx=MODEL_N_CTX, n_threads=MODEL_N_THREADS, verbose=False)
        print("Model loaded.", flush=True)
        for item in items:
            await process_item(client, token, item, llm)
            await asyncio.sleep(1)
    print("Done.", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
