import logging
import json
import re
from llama_cpp import Llama
from config import MODEL_PATH, MODEL_N_CTX, MODEL_N_THREADS, MAX_TOKENS, TEMPERATURE
from prompts import ANSWER_SYSTEM, SUMMARIZE_SYSTEM, QUERY_REFINE_SYSTEM, ENGAGEMENT_SYSTEM

logger = logging.getLogger(__name__)

def get_model():
    return Llama(
        model_path=MODEL_PATH,
        n_ctx=MODEL_N_CTX,
        n_threads=MODEL_N_THREADS,
        verbose=False
    )

def _extract_text(response) -> str:
    if isinstance(response, str): return response.strip()
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict):
            return choices[0].get("text", "").strip()
    return ""

def clean_artifacts(text: str) -> str:
    text = re.sub(r'\s*\[score:\s*\d+\]\s*:', ':', text)
    text = re.sub(r'\s*\[\d+\s*characters?\]', '', text)
    text = re.sub(r'\s*[!|/][tc]\b', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

def add_signature(reply: str, search_type: str = None) -> str:
    if not reply: return reply
    if search_type == "tavily": return f"{reply}\nQwen | Tavily"
    elif search_type == "chainbase": return f"{reply}\nQwen | Chainbase"
    return f"{reply}\nQwen"

def generate_digest(llm, raw_line: str, max_chars: int = 248) -> str:
    prompt = f"Refine the following crypto trend line for readability. STRICT CONSTRAINTS: MUST preserve exact prefix format 'Keyword: '. Total length MUST be under {max_chars} characters. Output ONLY the refined sentence. NO meta-text, character counts, or explanations. Do NOT add any score, emoji, or suffix. Input: {raw_line}\nOutput:"
    response = llm(prompt, max_tokens=120, temperature=0.3)
    return clean_artifacts(_extract_text(response))

def get_answer(llm, memory, context, search_results, user_text, do_search, search_type):
    clean_context = clean_artifacts(context)
    clean_user = clean_artifacts(user_text)
    prompt = f"{ANSWER_SYSTEM}\nContext:\n{clean_context}\nUser: {clean_user}\nAssistant:"
    response = llm(prompt, max_tokens=MAX_TOKENS, temperature=TEMPERATURE)
    return clean_artifacts(_extract_text(response))

def extract_search_params(llm, user_text, root_text):
    prompt = f"{QUERY_REFINE_SYSTEM}\nUser message: \"{user_text}\"\nContext: \"{root_text}\"\nOutput JSON:"
    response = llm(prompt, max_tokens=150, temperature=0.2)
    try:
        text = _extract_text(response)
        params = json.loads(text)
        params["query"] = clean_artifacts(params.get("query", ""))
        return params
    except:
        return {"query": clean_artifacts(user_text), "time_range": "w", "topic": "tech"}

def update_summary(llm, memory, user_text, reply):
    prompt = f"{SUMMARIZE_SYSTEM}\nQ: {user_text}\nA: {reply}\nSummary:"
    response = llm(prompt, max_tokens=50, temperature=0.3)
    return clean_artifacts(_extract_text(response))

async def generate_engagement_plan(llm, digest_text, comments):
    comments_text = "\n".join([f"@{c['handle']}: {c['text']}" for c in comments])
    prompt = f"{ENGAGEMENT_SYSTEM}\nPost: \"{digest_text}\"\nComments:\n{comments_text}\nJSON:"
    response = llm(prompt, max_tokens=200, temperature=0.3)
    try:
        text = _extract_text(response)
        return json.loads(text)
    except:
        return {"likes": [], "replies": []}
