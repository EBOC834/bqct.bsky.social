import os
from llama_cpp import Llama
import config
import prompts
from search import SOURCE_SUFFIXES

_llm = None

def get_model():
    global _llm
    if _llm is None:
        print(f"Loading {config.MODEL_PATH}...", flush=True)
        if not os.path.exists(config.MODEL_PATH):
            raise FileNotFoundError(f"Model not found: {config.MODEL_PATH}")
        _llm = Llama(
            model_path=config.MODEL_PATH,
            n_ctx=config.MODEL_N_CTX,
            n_threads=config.MODEL_N_THREADS,
            verbose=False,
            n_batch=512
        )
        print("Model loaded.", flush=True)
    return _llm

def generate(llm, prompt, max_tokens=None, stop=None, temperature=None):
    out = llm(
        prompt,
        max_tokens=max_tokens or config.MAX_TOKENS,
        temperature=temperature if temperature is not None else config.TEMPERATURE,
        stop=stop,
        echo=False
    )
    return out["choices"][0]["text"].strip()

def extract_search_intent(llm, user_text, max_tokens=20):
    prompt = (
        f"  system\nExtract the core search intent from the user message. Ignore greetings, filler words, and conversational context. Output ONLY the clean search query (1-5 words).\n  user\n{user_text}\n  assistant\n"
    )
    return generate(llm, prompt, max_tokens=max_tokens, temperature=0.0, stop=["\n", "  user"])

def extract_search_params(llm, user_text, max_tokens=60):
    prompt = (
        f"  system\nExtract search parameters from the user message. Output ONLY in this exact format:\nquery: <1-5 clean words>\ntime_range: <day|week|none>\ntopic: <news|none>\nIgnore greetings and filler.\n  user\n{user_text}\n  assistant\n"
    )
    raw = generate(llm, prompt, max_tokens=max_tokens, temperature=0.0, stop=["\n\n", "  user"])
    params = {"query": "", "time_range": None, "topic": None}
    for line in raw.strip().split("\n"):
        if line.startswith("query:"):
            params["query"] = line.split(":", 1)[1].strip()
        elif line.startswith("time_range:"):
            val = line.split(":", 1)[1].strip().lower()
            if val in ["day", "week"]:
                params["time_range"] = val
        elif line.startswith("topic:"):
            val = line.split(":", 1)[1].strip().lower()
            if val == "news":
                params["topic"] = "news"
    if not params["query"]:
        params["query"] = user_text.strip()[:50]
    return params

def format_reply(reply, do_search, search_type):
    reply = reply[:config.RESPONSE_MAX_CHARS]
    if do_search and search_type in SOURCE_SUFFIXES:
        suffix = SOURCE_SUFFIXES[search_type]
    else:
        suffix = "\n\nQwen"
    return reply.rstrip() + suffix

def generate_summary(llm, current_summary, interaction):
    prompt = (
        f"  system\n{prompts.SUMMARIZE_SYSTEM}\n"
        f"  user\nCurrent Summary:\n{current_summary}\n"
        f"New Interaction:\n{interaction}\n"
        f"Output only the new summary.\n"
        f"  assistant\n"
    )
    raw_summary = generate(llm, prompt, max_tokens=256, temperature=0.3, stop=["  user"])
    return raw_summary[:config.CONTEXT_MAX_CHARS]
