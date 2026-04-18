import os
import logging
import json
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

def _extract_text(response) -> str:
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict):
            return choices[0].get("text", "").strip()
    return ""

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
    return _extract_text(response)

def get_answer(llm, memory, context, search_results, user_text, do_search, search_type):
    prompt = f"{SYSTEM_PROMPT}\n\nContext:\n{context}\n\nUser: {user_text}\nAssistant:"
    response = llm(prompt, max_tokens=500, temperature=0.7)
    return _extract_text(response)

def extract_search_params(llm, user_text, root_text):
    prompt = f"""Extract search query and filters from: "{user_text}"
Context: "{root_text}"
Output JSON: {{"query": "...", "time_range": "...", "topic": "..."}}"""
    response = llm(prompt, max_tokens=100, temperature=0.2)
    try:
        text = _extract_text(response)
        return json.loads(text)
    except:
        return {"query": user_text, "time_range": "d", "topic": "news"}

def update_summary(llm, memory, user_text, reply):
    prompt = f"Summarize this exchange in 1 sentence:\nQ: {user_text}\nA: {reply}"
    response = llm(prompt, max_tokens=50, temperature=0.3)
    return _extract_text(response)

async def generate_engagement_plan(llm, digest_text, comments):
    comments_text = "\n".join([f"@{c['handle']}: {c['text']}" for c in comments])
    prompt = f"""Analyze comments on this post: "{digest_text}"
Comments:
{comments_text}

Return JSON: {{"likes": ["uri1", "uri2"], "replies": [{{"uri": "...", "text": "..."}}]}}
Only like positive/short comments. Reply only to substantive questions. Keep replies <150 chars."""
    response = llm(prompt, max_tokens=200, temperature=0.3)
    try:
        text = _extract_text(response)
        return json.loads(text)
    except:
        return {"likes": [], "replies": []}
