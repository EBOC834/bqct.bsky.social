import os
import logging
import re
from llama_cpp import Llama
import config
import prompts

logger = logging.getLogger(__name__)

def get_model():
    try:
        return Llama(
            model_path=config.MODEL_PATH,
            n_ctx=config.MODEL_N_CTX,
            n_threads=config.MODEL_N_THREADS,
            verbose=False
        )
    except Exception as e:
        logger.error(f"Model load failed: {e}")
        raise

def sanitize_input(text: str) -> str:
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
    return text.strip()[:2000]

def extract_search_params(llm, user_text):
    clean_text = sanitize_input(user_text)
    fp = f"  system\n{prompts.QUERY_REFINE_SYSTEM}\nuser\n{clean_text}\nassistant\n"
    out = llm(fp, max_tokens=64, stop=["  user", "  system", "  assistant"], echo=False, temperature=0.3)
    query = out["choices"][0]["text"].strip()
    params = {"query": query if query else clean_text[:100]}
    if "today" in clean_text.lower() or "now" in clean_text.lower():
        params["time_range"] = "day"
    if "news" in clean_text.lower():
        params["topic"] = "news"
    return params

def get_answer(llm, memory_context, fresh_context, search_results, user_text, do_search, search_type):
    full_context = ""
    if memory_context:
        full_context += f"Thread Summary:\n{memory_context}\n"
    if fresh_context:
        full_context += f"Thread Context:\n{fresh_context}\n"
    if search_results:
        full_context += f"Search Results:\n{search_results}\n"
    clean_question = sanitize_input(user_text)
    fp = f"  system\n{prompts.ANSWER_SYSTEM}\nuser\n{full_context}User Question:\n{clean_question}\nassistant\n"
    out = llm(fp, max_tokens=config.MAX_TOKENS, stop=["  user", "  system", "  assistant"], echo=False, temperature=config.TEMPERATURE)
    reply = out["choices"][0]["text"].strip()
    suffix_map = {"tavily": "Qwen | Tavily", "chainbase": "Qwen | Chainbase"}
    suffix = suffix_map.get(search_type, "Qwen") if do_search else "Qwen"
    final = f"{reply} {suffix}"
    return final[:config.RESPONSE_MAX_CHARS]

def update_summary(llm, old_summary, user_text, reply):
    fp = f"  system\n{prompts.SUMMARIZE_SYSTEM}\nuser\nPrevious: {old_summary}\nUser: {sanitize_input(user_text)}\nReply: {sanitize_input(reply)}\nNew summary:\nassistant\n"
    out = llm(fp, max_tokens=128, stop=["  user", "  system", "  assistant"], echo=False, temperature=0.5)
    return out["choices"][0]["text"].strip()[:300]

def generate_digest(llm, raw_line):
    prompt = (
        "Rewrite this crypto trend into a single, complete sentence under 260 chars. "
        "Format exactly: '- KEYWORD [RANK]: Summary.' End with ONE period. English only.\n"
        f"Input: {raw_line}\nOutput: - "
    )
    out = llm(prompt, max_tokens=80, stop=["\n", "Input:", "Output:"], echo=False, temperature=0.1)
    text = out["choices"][0]["text"].strip()
    if not text.startswith("- "):
        text = "- " + text
    text = text.rstrip('.!? ') + '.'
    return text[:260]
