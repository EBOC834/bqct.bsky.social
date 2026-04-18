import os
import logging
from llama_cpp import Llama

logger = logging.getLogger(__name__)
MODEL_PATH = os.getenv("MODEL_PATH", "models/qwen2.5-coder-14b-instruct-q5_k_m.gguf")
SYSTEM_PROMPT = "You are a concise, expert crypto/tech analyst. Answer strictly based on provided context."

def get_model():
    return Llama(
        model_path=MODEL_PATH,
        n_ctx=8192,
        n_threads=4,
        verbose=False
    )

def generate_digest(llm, raw_line: str, max_chars: int = 248) -> str:
    prompt = f"""Rewrite the following crypto trend into a concise, engaging sentence.

STRICT CONSTRAINTS:
- Total length MUST be under {max_chars} characters.
- Keep the score format exactly as [score:XXX].
- Preserve the core fact and impact.
- Use active, professional tone.
- Output ONLY the rewritten sentence.

Input: {raw_line}

Output:"""
    response = llm(prompt, max_tokens=120, temperature=0.3)
    return response.strip()

def get_answer(llm, memory, context, search_results, user_text, do_search, search_type):
    prompt = f"{SYSTEM_PROMPT}\n\nContext:\n{context}\n\nUser: {user_text}\nAssistant:"
    response = llm(prompt, max_tokens=500, temperature=0.7)
    return response.strip()

def extract_search_params(llm, user_text, root_text):
    prompt = f"""Extract search query and filters from: "{user_text}"
Context: "{root_text}"
Output JSON: {{"query": "...", "time_range": "...", "topic": "..."}}"""
    response = llm(prompt, max_tokens=100, temperature=0.2)
    try:
        import json
        return json.loads(response.strip())
    except:
        return {"query": user_text, "time_range": "d", "topic": "news"}

def update_summary(llm, memory, user_text, reply):
    prompt = f"Summarize this exchange in 1 sentence:\nQ: {user_text}\nA: {reply}"
    response = llm(prompt, max_tokens=50, temperature=0.3)
    return response.strip()

async def generate_engagement_plan(llm, digest_text, comments):
    comments_text = "\n".join([f"@{c['handle']}: {c['text']}" for c in comments])
    prompt = f"""Analyze comments on this post: "{digest_text}"
Comments:
{comments_text}

Return JSON: {{"likes": ["uri1", "uri2"], "replies": [{{"uri": "...", "text": "..."}}]}}
Only like positive/short comments. Reply only to substantive questions. Keep replies <150 chars."""
    response = llm(prompt, max_tokens=200, temperature=0.3)
    try:
        import json
        return json.loads(response.strip())
    except:
        return {"likes": [], "replies": []}
