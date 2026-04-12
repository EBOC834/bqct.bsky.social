import os
import sys
import json
import asyncio
import httpx
from llama_cpp import Llama
import bsky_utils
import prompts

# Config
MODEL_PATH = "models/Qwen3-14B-Q4_K_M.gguf"
MODEL_N_CTX = 2048
MODEL_N_THREADS = 2
TEMPERATURE = 0.7
MAX_TOKENS = 100
RESPONSE_MAX_CHARS = 300
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
BOT_DID = os.getenv("BOT_DID")

# Env Vars for Bsky
BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")

async def refine_query(llm, user_text, context_summary):
    """Uses LLM to create a better search query for !wa"""
    system_prompt = "You are a search query optimizer. Create a concise search query based on the user's question and context."
    user_prompt = f"Context: {context_summary}\nQuestion: {user_text}\nQuery:"
    
    try:
        out = llm(
            fp, 
            max_tokens=50, 
            stop=["<|im_end|>", "<|im_start|>"], 
            echo=False, 
            temperature=0.5,
            chat_template_kwargs={"reasoning": False} # FIX FOR REASONING
        )
        query = out["choices"][0]["text"].strip()
        return query[:200] if query else user_text[:200]
    except Exception as e:
        print(f"Refine error: {e}")
        return user_text[:200]

async def tavily_search(query):
    if not TAVILY_API_KEY:
        return ""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 3
                },
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                answer = data.get("answer", "")
                results = data.get("results", [])
                summary = f"AI Answer: {answer}\n" if answer else ""
                for res in results:
                    summary += f"- {res.get('title', '')}: {res.get('content', '')[:150]}...\n"
                return summary[:1000] # Limit context size
    except Exception as e:
        print(f"Tavily error: {e}")
    return ""

async def process_item(client, token, item, llm):
    uri = item["uri"]
    user_text = item["text"]
    do_search = item["has_search"]
    
    print(f"Processing: {user_text[:30]}...", flush=True)

    # Get Context
    rec = await bsky_utils.get_record(client, token, uri)
    if not rec:
        print("Failed to get record", flush=True)
        return

    reply_info = rec["value"].get("reply", {})
    root_uri = reply_info.get("root", {}).get("uri", uri)
    root_cid = reply_info.get("root", {}).get("cid", "")
    parent_cid = rec.get("cid", "")

    # Fetch Thread History
    thread_posts = await bsky_utils.get_thread_context(client, token, root_uri)
    
    # Build Context Summary
    context_str = ""
    for post in thread_posts[-5:]: # Last 5 messages
        context_str += f"@{post['handle']}: {post['text']}\n"
    
    # Add Link Metadata if present
    if "http" in user_text:
        urls = [u for u in user_text.split() if u.startswith("http")]
        if urls:
            meta = await bsky_utils.extract_link_metadata(urls[0])
            if meta["title"]:
                context_str += f"[Link Info: {meta['title']}]\n"

    # Search Logic
    search_results = ""
    if do_search:
        query = user_text.replace("!w", "").replace("!wa", "").strip()
        if "!wa" in user_text.lower():
            # Refine query using LLM
            print("Refining query with AI...", flush=True)
            query = await refine_query(llm, query, context_str)
            print(f"Refined Query: {query}", flush=True)
        
        print(f"Searching Tavily for: {query}", flush=True)
        search_results = await tavily_search(query)

    # Generate Reply
    personality = prompts.ANSWER_PROMPTS[1] # Default friendly
    system_prompt = f"{personality}\nMax length: {RESPONSE_MAX_CHARS} chars."
    
    final_prompt = f"Context:\n{context_str}\nSearch Results:\n{search_results}\nUser Question:\n{user_text}\nAnswer:"

    try:
        out = llm(
            fp,
            max_tokens=MAX_TOKENS,
            stop=["<|im_end|>", "<|im_start|>"],
            echo=False,
            temperature=TEMPERATURE,
            chat_template_kwargs={"reasoning": False} # THE FIX
        )
        reply = out["choices"][0]["text"].strip()
        reply = reply[:RESPONSE_MAX_CHARS]
        
        print(f"Reply: {reply}", flush=True)

        # Post Reply
        await bsky_utils.post_reply(
            client, token, BOT_DID, reply, root_uri, root_cid, uri, parent_cid
        )
        print("Posted!", flush=True)

    except Exception as e:
        print(f"Generation/Post error: {e}", flush=True)

async def main():
    # 1. Check for work
    if not os.path.exists("work_data.json"):
        print("No work_data.json found. Exiting without loading model.", flush=True)
        sys.exit(0)

    # 2. Load Work Data
    with open("work_data.json", "r") as f:
        work_data = json.load(f)
    
    items = work_data.get("items", [])
    if not items:
        print("Work data empty.", flush=True)
        sys.exit(0)

    # 3. Initialize Bsky Client
    async with bsky_utils.get_client() as client:
        token = await bsky_utils.login(client, BOT_HANDLE, BOT_PASSWORD)
        
        # 4. Load Model (Heavy Operation)
        print("Loading Qwen3-14B...", flush=True)
        llm = Llama(
            model_path=MODEL_PATH,
            n_ctx=MODEL_N_CTX,
            n_threads=MODEL_N_THREADS,
            verbose=False,
            chat_template_kwargs={"reasoning": False} # Global fix
        )
        print("Model loaded.", flush=True)

        # 5. Process Items
        for item in items:
            await process_item(client, token, item, llm)
            await asyncio.sleep(1) # Rate limit safety

        print("Done.", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
