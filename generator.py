import os
import logging
import json
import re
from llama_cpp import Llama

logger = logging.getLogger(__name__)

MODEL_PATH = os.getenv("MODEL_PATH", "models/qwen2.5-coder-14b-instruct-q5_k_m.gguf")
MODEL_N_CTX = int(os.getenv("MODEL_N_CTX", "8192"))
MODEL_N_THREADS = int(os.getenv("MODEL_N_THREADS", "2"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "512"))

def get_signature(search_type: str = None) -> str:
    if search_type == "tavily": return "\n\nQwen | Tavily"
    elif search_type == "chainbase": return "\n\nQwen | Chainbase"
    return "\n\nQwen"

def get_max_reply_chars(search_type: str = None) -> int:
    sig = get_signature(search_type)
    return 300 - len(sig)

SYSTEM_PROMPT = "You are a concise, expert crypto/tech analyst. Answer strictly based on provided context. Prioritize [ROOT] post. If asked for 'other' or 'different' news, avoid repeating thread context. Synthesize ONLY new info from search. If unknown, state so. Output only final answer."
SUMMARIZE_SYSTEM = "Maintain concise thread summary. Preserve [ROOT] anchor. Update with essential reply info. Remove redundancy. Keep under 300 chars excluding [ROOT]. Output only summary text."
QUERY_REFINE_SYSTEM = """You are a search query optimizer. Extract a concise, factual search query from the user's question based on thread context.

CRITICAL RULES:
1. If user says "something else", "another question", or references previous answers, IGNORE the root post content and infer the NEW topic from the user's intent.
2. Ignore filler words, mentions, triggers (!t, !c), and meta-requests like "tell me a simple sentence".
3. Focus on what the user is ACTUALLY asking about, not what words appear literally in the text.
4. If the root post is about X but user asks about Y, query should be about Y.
5. Return ONLY valid JSON with keys: "query", "time_range", "topic".
6. For "time_range": use "day", "week", "month", "year", or null.
7. For "topic": use "news", "finance", or null (null = general, use by default).
8. Output ONLY the JSON object, no explanations, no markdown.

User message: "{user_text}"
Context: "{root_text}"
Output JSON:"""

DIGEST_REFINE_SYSTEM = """Write a concise description for the crypto trend "{keyword}".

RULES:
1. DO NOT repeat "{keyword}" or variations. Start directly with the insight.
2. Focus on the core fact/update from the context.
3. STRICTLY under {max_desc_chars} characters.
4. Output ONLY the description text.

Context: {summary}
Output:"""

ENGAGEMENT_SYSTEM = "Analyze comments on digest. Return JSON: {\"likes\": [\"uri1\"], \"replies\": [{\"uri\": \"...\", \"text\": \"...\"}]}. Like positive/short comments. Reply only to substantive questions. Replies <150 chars."

def get_model():
    return Llama(model_path=MODEL_PATH, n_ctx=MODEL_N_CTX, n_threads=MODEL_N_THREADS, verbose=False)

def _extract_text(response) -> str:
    if isinstance(response, str): return response.strip()
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict): return choices[0].get("text", "").strip()
    return ""

def clean_artifacts(text: str) -> str:
    text = re.sub(r'\s*\[score:\s*\d+\]\s*:', ':', text)
    text = re.sub(r'\s*\[\d+\s*characters?\]', '', text)
    text = re.sub(r'\s*[!|/][tc]\b', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

def generate_digest(llm, keyword: str, summary: str, max_desc_chars: int) -> str:
    prompt = f"""Write a concise description for the crypto trend "{keyword}".

RULES:
1. DO NOT repeat "{keyword}" or variations. Start directly with the insight.
2. Focus on the core fact/update from the context.
3. STRICTLY under {max_desc_chars} characters.
4. Output ONLY the description text.

Context: {summary}
Output:"""
    response = llm(prompt, max_tokens=min(max_desc_chars + 10, 100), temperature=0.3)
    raw = clean_artifacts(_extract_text(response))
    desc = raw.split('\n')[0].strip()
    if len(desc) > max_desc_chars:
        desc = desc[:max_desc_chars-3].rsplit(' ', 1)[0] + "..."
    return desc

def get_answer(llm, memory, context, search_results, user_text, do_search, search_type, max_chars: int):
    signature = get_signature(search_type)
    clean_context = clean_artifacts(context)
    clean_user = clean_artifacts(user_text)
    prompt = f"""{SYSTEM_PROMPT}

STRICT OUTPUT LIMIT: Your entire response must be under {max_chars} characters. This limit is non-negotiable — Bluesky will reject longer posts.

Context:
{clean_context}

User: {clean_user}

Answer (under {max_chars} chars, no signature needed):"""
    response = llm(prompt, max_tokens=min(max_chars + 50, MAX_TOKENS), temperature=TEMPERATURE)
    raw = clean_artifacts(_extract_text(response))
    reply = raw.split('\n')[0].strip() if '\n' in raw else raw
    if len(reply) > max_chars:
        reply = reply[:max_chars-3].rsplit(' ', 1)[0] + "..."
        logger.warning(f"[LLM] Reply truncated: {len(raw)} → {len(reply)} chars")
    return reply

def extract_search_params(llm, user_text, root_text):
    prompt = f"{QUERY_REFINE_SYSTEM.format(user_text=user_text, root_text=root_text)}"
    response = llm(prompt, max_tokens=150, temperature=0.2)
    try:
        text = _extract_text(response)
        params = json.loads(text)
        params["query"] = clean_artifacts(params.get("query", ""))
        return params
    except:
        return {"query": clean_artifacts(user_text), "time_range": None, "topic": None}

def update_summary(llm, memory, user_text, reply):
    prompt = f"{SUMMARIZE_SYSTEM}\nQ: {user_text}\nA: {reply}\nSummary:"
    response = llm(prompt, max_tokens=50, temperature=0.3)
    return clean_artifacts(_extract_text(response))

async def generate_engagement_plan(llm, digest_text, comments):
    comments_text = "\n".join([f"@{c['handle']}: {c['text']}" for c in comments])
    prompt = f"{ENGAGEMENT_SYSTEM}\nPost: \"{digest_text}\"\nComments:\n{comments_text}\nJSON:"
    response = llm(prompt, max_tokens=200, temperature=0.3)
    try: return json.loads(_extract_text(response))
    except: return {"likes": [], "replies": []}
