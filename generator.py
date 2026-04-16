import os
import re
from llama_cpp import Llama
import config
import prompts

def get_model():
    return Llama(
        model_path=config.MODEL_PATH,
        n_ctx=config.MODEL_N_CTX,
        n_threads=config.MODEL_N_THREADS,
        verbose=False
    )

def extract_search_params(llm, user_text, root_post_text=""):
    clean_user_text = re.sub(r'\s*![tc]\b', '', user_text, flags=re.IGNORECASE).strip()
    
    if root_post_text:
        clean_root = re.sub(r'\s*![tc]\b', '', root_post_text, flags=re.IGNORECASE).strip()
        context_for_query = f"Topic: {clean_root}\nQuestion: {clean_user_text}"
    else:
        context_for_query = clean_user_text
    
    fp = f"  system\n{prompts.QUERY_REFINE_SYSTEM}\nuser\n{context_for_query}\nassistant\n"
    out = llm(fp, max_tokens=64, stop=["  user", "  system", "  assistant"], echo=False, temperature=0.3)
    query = out["choices"][0]["text"].strip()
    
    query = re.sub(r'\s*![tc]\b', '', query, flags=re.IGNORECASE).strip()
    
    params = {"query": query if query else clean_user_text[:100]}
    if "today" in clean_user_text.lower() or "now" in clean_user_text.lower():
        params["time_range"] = "day"
    if "news" in clean_user_text.lower():
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
    
    fp = f"  system\n{prompts.ANSWER_SYSTEM}\nuser\n{full_context}User Question:\n{user_text}\nassistant\n"
    out = llm(fp, max_tokens=config.MAX_TOKENS, stop=["  user", "  system", "  assistant"], echo=False, temperature=config.TEMPERATURE)
    reply = out["choices"][0]["text"].strip()
    
    if do_search and search_type == "tavily":
        suffix = "Qwen | Tavily"
    elif do_search and search_type == "chainbase":
        suffix = "Qwen | Chainbase"
    else:
        suffix = "Qwen"
    
    max_reply_chars = config.RESPONSE_MAX_CHARS - len(suffix) - 2
    reply = reply[:max_reply_chars]
    return f"{reply}\n\n{suffix}"

def update_summary(llm, old_summary, user_text, reply):
    fp = f"  system\n{prompts.SUMMARIZE_SYSTEM}\nuser\nPrevious: {old_summary}\nUser: {user_text}\nReply: {reply}\nNew summary:\nassistant\n"
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
