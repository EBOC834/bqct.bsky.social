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

SYSTEM_PROMPT = "You are a concise, expert crypto/tech analyst. Answer strictly based on provided context. Prioritize [ROOT] post. If asked for 'other' or 'different' news, avoid repeating thread context. Synthesize ONLY new info from search. If unknown, state so. Output only final answer."
SUMMARIZE_SYSTEM = "Maintain concise thread summary. Preserve [ROOT] anchor. Update with essential reply info. Remove redundancy. Keep under 300 chars excluding [ROOT]. Output only summary text."
QUERY_REFINE_SYSTEM = "Extract concise search query from user question. Return only query string. Remove fillers, mentions, triggers. Identify core entities/topics. Output ONLY valid JSON: {\"query\": \"...\", \"time_range\": \"d|w|m|null\", \"topic\": \"tech|crypto|news|null\"}"
DIGEST_REFINE_SYSTEM = "Refine this crypto trend into a compelling headline. Replace generic 'Keyword:' with the actual topic name. Keep under {max_chars} chars. Output ONLY the refined sentence, no prefix, no score, no emoji."
ENGAGEMENT_SYSTEM = "Analyze comments on digest. Return JSON: {\"likes\": [\"uri1\"], \"replies\": [{\"uri\": \"...\", \"text\": \"...\"}]}. Like positive/short comments. Reply only to substantive questions. Replies <150 chars."

def get_model():
    return Llama(
        model_path=MODEL_PATH,
        n_ctx=MODEL_N_CTX,
        n_threads=MODEL_N_THREADS,
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

def clean_artifacts(text: str) -> str:
    text = re.sub(r'\s*\[score:\s*\d+\]\s*:', ':', text)
    text = re.sub(r'\s*\[\d+\s*characters?\]', '', text)
    text = re.sub(r'\s*[!|/][tc]\b', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()

def add_signature(reply: str, search_type: str = None) -> str:
    if not reply:
        return reply
    if search_type == "tavily":
        return f"{reply}\nQwen | Tavily"
    elif search_type == "chainbase":
        return f"{reply}\nQwen | Chainbase"
    return f"{reply}\nQwen"

def generate_digest(llm, raw_line: str, max_chars: int = 248) -> str:
    prompt = f"{DIGEST_REFINE_SYSTEM.format(max_chars=max_chars)}\nInput: {raw_line}\nOutput:"
    response = llm(prompt, max_tokens=120, temperature=0.3)
    return clean_artifacts(_extract_text(response))

def get_answer(llm, memory, context, search_results, user_text, do_search, search_type):
    clean_context = clean_artifacts(context)
    clean_user = clean_artifacts(user_text)
    prompt = f"{SYSTEM_PROMPT}\nContext:\n{clean_context}\nUser: {clean_user}\nAssistant:"
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
