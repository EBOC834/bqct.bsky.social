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

SYSTEM_PROMPT = """You are a concise, expert crypto/tech analyst. Answer strictly based on provided context.

PRIORITY RULES:
1. [User Question] has HIGHEST priority — answer THIS first.
2. If user says "something else", "another question", or "different topic", IGNORE previous context and focus on inferring the NEW intent.
3. Use [Search Results] for fresh data, [ROOT] for original topic context.
4. If context is unclear after 2+ "something else" messages, ask for clarification.
5. Keep answers under the character limit provided. Output only the final answer.

SEARCH HANDLING:
- If [Search Results] directly answers the user's question, synthesize it concisely.
- If [Search Results] is empty, irrelevant, or unrelated to the core question, IGNORE IT COMPLETELY and answer using thread context only.
- Never let irrelevant search results override the user's clear intent.

FORMAT RULES:
- NEVER output bracketed markers like [ROOT], [User Question], [Memory], [Search Results] in your response.
- These markers are for context structure only. Your answer must be plain text only.
- Do not prefix your answer with @handle, [ROOT], or any metadata.

Output only the final answer."""

SUMMARIZE_SYSTEM = "Maintain concise thread summary. Preserve [ROOT] anchor. Update with essential reply info. Remove redundancy. Keep under 300 chars excluding [ROOT]. Output only summary text."

QUERY_REFINE_SYSTEM = """You are a search query optimizer. Extract a concise, factual search query from the user's question based on thread context.

CRITICAL RULES:
1. [User Question] has HIGHEST priority, BUT you MUST resolve references like "these", "them", "those", "it", "this" by looking at RECENT messages in the thread context.
2. If user says "these services", "those tools", "them", infer the referent from the 1-2 most recent owner messages in context.
3. If user says "something else", "another question", "different topic", or similar 1+ times: 
   - IGNORE [ROOT] content completely
   - Infer the NEW topic from user's intent and recent thread context
   - If intent is still unclear after 2+ such messages, output: {{"query": "clarify new topic", "time_range": null, "topic": null}}
4. For Chainbase (!c) searches: extract ONLY the core keyword or ticker (e.g., "BTC", "ETH", "RWA", "AI Agent"). Remove all filler words, questions, and meta-text. Output a single word or short phrase.
5. Ignore filler words, mentions, triggers (!t, !c), and meta-requests like "tell me a simple sentence".
6. Focus on what the user is ACTUALLY asking about, not literal words in the text.
7. Return ONLY valid JSON with keys: "query", "time_range", "topic".
8. For "time_range": use "day", "week", "month", "year", "d", "w", "m", "y", or null.
9. For "topic": 
   - Use null (DEFAULT) for general search — this is the default for most queries.
   - Use "news" ONLY if user explicitly asks for news/updates/latest developments.
   - Use "finance" ONLY if user explicitly asks about markets/trading/financial data.
   - NEVER use "tech", "crypto", "technology", or any other value — these are invalid.
10. Output ONLY the JSON object, no explanations, no markdown.

Thread Context: {{context}}
User message: "{{user_text}}"
Output JSON:"""

DIGEST_REFINE_SYSTEM = """Write a concise description for the crypto trend "{keyword}".

HARD CONSTRAINT: Your output MUST be strictly under {max_desc_chars} characters. This is non-negotiable.

RULES:
1. DO NOT repeat "{keyword}" or variations. Start directly with the insight.
2. Focus on the core fact/update from the context: price action, volume, catalyst, outlook.
3. Use short, factual sentences. Avoid connectors like "however", "furthermore", "additionally".
4. End at a complete thought — do not cut mid-sentence.
5. Output ONLY the description text, no quotes, no markers.

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
    text = re.sub(r'^\[ROOT\]\s*@[^\s]+:\s*', '', text)
    text = re.sub(r'^\[[A-Z_]+\]\s*', '', text)
    return text.strip()

def generate_digest(llm, keyword: str, summary: str, max_desc_chars: int) -> str:
    safety_margin = 15
    target_chars = max(20, max_desc_chars - safety_margin)
    prompt = f"""Write a concise description for the crypto trend "{keyword}".

HARD CONSTRAINT: Your output MUST be strictly under {target_chars} characters.

RULES:
1. DO NOT repeat "{keyword}" or variations. Start directly with the insight.
2. Focus on the core fact: price action, volume, catalyst, outlook.
3. Use short, factual sentences. Avoid connectors.
4. End at a complete thought — do not cut mid-sentence.
5. Output ONLY the description text.

Context: {summary}
Output:"""
    response = llm(prompt, max_tokens=min(target_chars + 20, 100), temperature=0.3)
    raw = clean_artifacts(_extract_text(response))
    desc = raw.split('\n')[0].strip()
    if len(desc) > max_desc_chars:
        desc = desc[:max_desc_chars]
        last_period = desc.rfind('.')
        last_space = desc.rfind(' ')
        cut_point = max(last_period, last_space)
        if cut_point > max_desc_chars * 0.7:
            desc = desc[:cut_point].rstrip('.,;:')
        else:
            desc = desc[:max_desc_chars].rstrip('.,;:')
    return desc

def get_answer(llm, memory, context, search_results, user_text, do_search, search_type, max_chars: int):
    signature = get_signature(search_type)
    clean_context = clean_artifacts(context)
    clean_user = clean_artifacts(user_text)
    safe_max_tokens = max(int(max_chars * 0.6), 50)
    prompt = f"""{SYSTEM_PROMPT}

CRITICAL CONSTRAINT: Your response MUST be strictly under {max_chars} characters. This is a hard limit enforced by the platform. Do not add greetings, sign-offs, or extra text. Stop exactly when complete.

Context:
{clean_context}

User: {clean_user}

Answer:"""
    response = llm(prompt, max_tokens=safe_max_tokens, temperature=TEMPERATURE)
    raw = clean_artifacts(_extract_text(response))
    reply = raw.split('\n')[0].strip()
    if len(reply) > max_chars:
        reply = reply[:max_chars].rsplit(' ', 1)[0]
    return reply

def extract_search_params(llm, context, user_text):
    prompt = QUERY_REFINE_SYSTEM.replace("{{context}}", context).replace("{{user_text}}", user_text)
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
