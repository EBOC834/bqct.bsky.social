import os
import time
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
    print(f"[GEN] Extracting search params for: {user_text[:100]}...", flush=True)
    prompt = f"{prompts.QUERY_REFINE_SYSTEM}\n\nUser: {user_text}\nQuery:"
    fp = f"  system\n{prompts.QUERY_REFINE_SYSTEM}\n  user\n{user_text}\n  assistant\n"
    out = llm(fp, max_tokens=64, stop=["  user", "  system", "  assistant"], echo=False, temperature=0.3)
    query = out["choices"][0]["text"].strip()
    params = {"query": query if query else user_text[:100]}
    if "today" in user_text.lower() or "now" in user_text.lower():
        params["time_range"] = "day"
    if "news" in user_text.lower():
        params["topic"] = "news"
    print(f"[GEN] Generated search query: {params['query']}", flush=True)
    return params

def get_answer(llm, memory_context, fresh_context, search_results, user_text, do_search, search_type):
    print(f"[GEN] === START ANSWER GENERATION ===", flush=True)
    print(f"[GEN] User input: {user_text[:150]}...", flush=True)
    print(f"[GEN] Memory context: {memory_context[:200] if memory_context else 'None'}...", flush=True)
    print(f"[GEN] Fresh context: {fresh_context[:200] if fresh_context else 'None'}...", flush=True)
    print(f"[GEN] Search results: {search_results[:200] if search_results else 'None'}...", flush=True)

    full_context = ""
    if memory_context:
        full_context += f"Thread Summary:\n{memory_context}\n\n"
    if fresh_context:
        full_context += f"Thread Context:\n{fresh_context}\n\n"
    if search_results:
        full_context += f"Search Results:\n{search_results}\n\n"

    prompt = f"{prompts.ANSWER_SYSTEM}\n\n{full_context}User Question:\n{user_text}"
    print(f"[GEN] Full prompt length: {len(prompt)} chars", flush=True)
    print(f"[GEN] System prompt: {prompts.ANSWER_SYSTEM[:150]}...", flush=True)

    fp = f"  system\n{prompts.ANSWER_SYSTEM}\n  user\n{prompt}\n  assistant\n"
    start = time.time()
    out = llm(fp, max_tokens=config.MAX_TOKENS, stop=["  user", "  system", "  assistant"], echo=False, temperature=config.TEMPERATURE)
    elapsed = time.time() - start
    raw_reply = out["choices"][0]["text"].strip()
    print(f"[GEN] LLM raw output ({elapsed:.1f}s): {raw_reply[:200]}...", flush=True)

    if do_search and search_type == "tavily":
        suffix = "Qwen | Tavily"
    elif do_search and search_type == "chainbase":
        suffix = "Qwen | Chainbase"
    else:
        suffix = "Qwen"

    final = f"{raw_reply} {suffix}"
    if len(final) > config.RESPONSE_MAX_CHARS:
        final = final[:config.RESPONSE_MAX_CHARS].rsplit(' ', 1)[0]
    print(f"[GEN] Final answer ({len(final)} chars): {final[:150]}...", flush=True)
    print(f"[GEN] === END ANSWER GENERATION ===", flush=True)
    return final

def update_summary(llm, old_summary, user_text, reply):
    print(f"[GEN] Updating thread summary...", flush=True)
    prompt = f"{prompts.SUMMARIZE_SYSTEM}\n\nPrevious: {old_summary}\nUser: {user_text}\nReply: {reply}\nNew summary:"
    fp = f"  system\n{prompts.SUMMARIZE_SYSTEM}\n  user\n{prompt}\n  assistant\n"
    out = llm(fp, max_tokens=128, stop=["  user", "  system", "  assistant"], echo=False, temperature=0.5)
    new_summary = out["choices"][0]["text"].strip()[:300]
    print(f"[GEN] Extracted context for future: {new_summary[:150]}...", flush=True)
    return new_summary

def generate_digest(llm, raw_line):
    print(f"[GEN] Generating digest for: {raw_line[:100]}...", flush=True)
    prompt = (
        "Rewrite this crypto trend into a single, complete sentence under 260 chars. "
        "Format exactly: '- KEYWORD [score:XXX]: Summary.' End with ONE period. English only.\n\n"
        f"Input: {raw_line}\n\n"
        "Output: - "
    )
    out = llm(prompt, max_tokens=80, stop=["\n", "Input:", "Output:"], echo=False, temperature=0.1)
    text = out["choices"][0]["text"].strip()
    if not text.startswith("- "):
        text = "- " + text
    text = text.rstrip('.!? ') + '.'
    final = text[:260]
    print(f"[GEN] Digest output: {final}", flush=True)
    return final
