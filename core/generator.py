import os
import json
import re
from llama_cpp import Llama
from core.config import MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS, TEMPERATURE, MAX_TOKENS, PLATFORM_LIMIT, load_prompts
from core.utils import clean_artifacts

PROMPTS = load_prompts()

def get_model():
    return Llama(model_path=MODEL_PATH, n_ctx=MODEL_N_CTX, n_threads=MODEL_N_THREADS, verbose=False)

def _extract_text(response):
    if isinstance(response, str): return response.strip()
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict): return choices[0].get("text", "").strip()
    return ""

def get_signature(stype=None):
    if stype == "tavily": return "\nQwen | Tavily"
    if stype == "chainbase": return "\nQwen | Chainbase"
    return "\nQwen"

def get_answer(llm, memory, context, search_results, user_text, do_search, search_type, max_chars):
    sig = get_signature(search_type if do_search else None)
    sys_prompt = PROMPTS.get("system", "")
    prompt = f"{sys_prompt}\nCRITICAL: Output under {max_chars} chars.\nContext:\n{clean_artifacts(context)}\nUser: {clean_artifacts(user_text)}\nAnswer:"
    safe_tokens = max(int(max_chars * 0.6), 50)
    response = llm(prompt, max_tokens=safe_tokens, temperature=TEMPERATURE)
    raw = clean_artifacts(_extract_text(response))
    reply = raw.split("\n")[0].strip()
    if len(reply) > max_chars:
        reply = reply[:max_chars].rsplit(' ', 1)[0]
    return f"{reply}{sig}"

def extract_search_params(llm, context, user_text):
    prompt = PROMPTS.get("query_refine", "").replace("{context}", context).replace("{user_text}", user_text)
    try:
        response = llm(prompt, max_tokens=150, temperature=0.2)
        params = json.loads(_extract_text(response))
        params["query"] = clean_artifacts(params.get("query", ""))
        return params
    except:
        return {"query": clean_artifacts(user_text), "time_range": None, "topic": None}

def update_summary(llm, memory, user_text, reply):
    prompt = f"Summarize thread. Preserve root context. Update with reply info. Remove redundancy. Keep under 250 chars.\nQ: {user_text}\nA: {reply}\nSummary:"
    response = llm(prompt, max_tokens=50, temperature=0.3)
    return clean_artifacts(_extract_text(response))

def generate_engagement_plan(llm, digest_text, comments):
    comments_text = "\n".join([f"@{c['handle']}: {c['text']}" for c in comments])
    prompt = PROMPTS.get("engagement_plan", "").replace("{post_text}", digest_text).replace("{comments}", comments_text)
    try: return json.loads(_extract_text(llm(prompt, max_tokens=200, temperature=0.3)))
    except: return {"likes": [], "replies": []}

def generate_digest_desc(llm, keyword, summary, max_chars):
    prompt = PROMPTS.get("digest_refine", "").replace("{keyword}", keyword).replace("{summary}", summary).replace("{max_chars}", str(max_chars))
    response = llm(prompt, max_tokens=min(max_chars + 20, 100), temperature=0.3)
    desc = clean_artifacts(_extract_text(response)).split("\n")[0].strip()
    if len(desc) > max_chars:
        desc = desc[:max_chars].rsplit(' ', 1)[0]
    return desc
