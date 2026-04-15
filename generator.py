import os
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

def extract_search_params(llm, user_text):
    prompt = f"{prompts.QUERY_REFINE_SYSTEM}\n\nUser: {user_text}\nQuery:"
    fp = f"  system\n{prompts.QUERY_REFINE_SYSTEM}\n  user\n{user_text}\n  assistant\n"
    out = llm(fp, max_tokens=64, stop=["  user", "  system", "  assistant"], echo=False, temperature=0.3)
    query = out["choices"][0]["text"].strip()
    params = {"query": query if query else user_text[:100]}
    if "today" in user_text.lower() or "now" in user_text.lower():
        params["time_range"] = "day"
    if "news" in user_text.lower():
        params["topic"] = "news"
    return params

def get_answer(llm, memory_context, fresh_context, search_results, user_text, do_search, search_type):
    full_context = ""
    if memory_context:
        full_context += f"Thread Summary:\n{memory_context}\n\n"
    if fresh_context:
        full_context += f"Thread Context:\n{fresh_context}\n\n"
    if search_results:
        full_context += f"Search Results:\n{search_results}\n\n"
    
    prompt = f"{prompts.ANSWER_SYSTEM}\n\n{full_context}User Question:\n{user_text}"
    fp = f"  system\n{prompts.ANSWER_SYSTEM}\n  user\n{prompt}\n  assistant\n"
    
    print(f"[GENERATOR] Prompt length: {len(prompt)} chars")
    print(f"[GENERATOR] Context preview: {full_context[:300]}...")
    
    out = llm(fp, max_tokens=config.MAX_TOKENS, stop=["  user", "  system", "  assistant"], echo=False, temperature=config.TEMPERATURE)
    reply = out["choices"][0]["text"].strip()
    
    print(f"[GENERATOR] Generated reply ({len(reply)} chars): {reply}")
    
    suffix = ""
    if do_search and search_type in ["tavily", "chainbase"]:
        from search import SOURCE_SUFFIXES
        suffix = SOURCE_SUFFIXES.get(search_type, "")
    final = f"{reply}{suffix}"
    return final[:config.RESPONSE_MAX_CHARS]

def update_summary(llm, old_summary, user_text, reply):
    prompt = f"{prompts.SUMMARIZE_SYSTEM}\n\nPrevious: {old_summary}\nUser: {user_text}\nReply: {reply}\nNew summary:"
    fp = f"  system\n{prompts.SUMMARIZE_SYSTEM}\n  user\n{prompt}\n  assistant\n"
    out = llm(fp, max_tokens=128, stop=["  user", "  system", "  assistant"], echo=False, temperature=0.5)
    return out["choices"][0]["text"].strip()[:300]
