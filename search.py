import httpx
import logging
import re
import json as json_lib
from config import TAVILY_API_KEY, SEARCH_TIMEOUT

logger = logging.getLogger(__name__)

def clean_query(query: str) -> str:
    query = re.sub(r'\s*[!|/][tc]\s*', ' ', query)
    query = re.sub(r'\s+', ' ', query)
    return query.strip()

async def tavily_search(query: str, time_range: str = None, topic: str = None) -> str:
    logger.info(f"[SEARCH] Tavily request | query='{query}' | time_range={time_range} | topic={topic}")
    if not TAVILY_API_KEY:
        logger.error("[SEARCH] TAVILY_API_KEY not set")
        return "Error: TAVILY_API_KEY not set"
    try:
        clean_q = clean_query(query)
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": clean_q,
            "search_depth": "basic",
            "max_results": 3,
            "include_answer": "basic",
            "include_raw_content": "text"
        }
        # Добавляем параметры ТОЛЬКО если они валидные (не None и не строка "null"/"None")
        if time_range and str(time_range).lower() not in ["null", "none", ""]:
            payload["time_range"] = str(time_range).lower()
            logger.debug(f"[SEARCH] Added time_range={payload['time_range']}")
        if topic and str(topic).lower() not in ["null", "none", ""]:
            payload["topic"] = str(topic).lower()
            logger.debug(f"[SEARCH] Added topic={payload['topic']}")
        
        # Полное логирование запроса
        logger.debug(f"[SEARCH] Tavily full payload: {json_lib.dumps(payload, indent=2)}")
        logger.debug(f"[SEARCH] Tavily URL: https://api.tavily.com/search")
        
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.tavily.com/search", json=payload, timeout=SEARCH_TIMEOUT)
            logger.debug(f"[SEARCH] Tavily response status: {r.status_code}")
            logger.debug(f"[SEARCH] Tavily response headers: {dict(r.headers)}")
            if r.status_code != 200:
                logger.error(f"[SEARCH] Tavily error: {r.status_code} | {r.text[:500]}")
                return f"Error: Tavily API returned {r.status_code}"
            data = r.json()
            logger.debug(f"[SEARCH] Tavily response keys: {list(data.keys())}")
            if data.get("answer"):
                logger.info(f"[SEARCH] Tavily answer preview: {data['answer'][:150]}...")
            if data.get("results"):
                logger.info(f"[SEARCH] Tavily results count: {len(data['results'])}")
                for i, res in enumerate(data["results"]):
                    logger.debug(f"[SEARCH] Result #{i+1} | title={res.get('title', '')[:50]} | url={res.get('url', '')[:50]} | score={res.get('score')}")
            return r.text
    except httpx.HTTPStatusError as e:
        logger.error(f"[SEARCH] Tavily HTTP error: {e} | response={e.response.text[:500] if e.response else 'N/A'}")
        return f"Error: {str(e)}"
    except Exception as e:
        logger.error(f"[SEARCH] Tavily exception: {e}")
        import traceback
        logger.debug(traceback.format_exc())
        return f"Error: {str(e)}"

async def chainbase_search(query: str) -> list:
    logger.info(f"[SEARCH] Chainbase request | query='{query}'")
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://api.chainbase.com/tops/v1/tool/list-trending-topics?language=en", timeout=SEARCH_TIMEOUT)
            logger.debug(f"[SEARCH] Chainbase response status: {r.status_code}")
            if r.status_code != 200:
                logger.error(f"[SEARCH] Chainbase error: {r.status_code}")
                return []
            data = r.json()
            items = data.get("items", [])
            logger.info(f"[SEARCH] Chainbase results count: {len(items)}")
            if items:
                logger.debug(f"[SEARCH] Top result | keyword={items[0].get('keyword')} | score={items[0].get('score')}")
            return items[:6]
    except Exception as e:
        logger.error(f"[SEARCH] Chainbase exception: {e}")
        return []

def is_search_result_valid(result, search_type: str) -> bool:
    if not result:
        logger.debug(f"[SEARCH] Result invalid: empty | type={search_type}")
        return False
    if search_type == "tavily":
        if "Error" in str(result) or len(str(result)) < 10:
            logger.debug(f"[SEARCH] Result invalid: error or too short | type={search_type}")
            return False
        logger.debug(f"[SEARCH] Result valid | type={search_type} | len={len(str(result))}")
        return True
    if search_type == "chainbase":
        valid = isinstance(result, list) and len(result) > 0
        logger.debug(f"[SEARCH] Result valid={valid} | type={search_type} | count={len(result) if isinstance(result, list) else 0}")
        return valid
    logger.debug(f"[SEARCH] Result valid | type={search_type}")
    return True

async def execute_if_needed(llm, item, root_text):
    if not item.get("has_search"):
        logger.debug("[SEARCH] Skip: has_search=False")
        return ""
    search_type = item.get("search_type", "tavily")
    user_text = item.get("text", "")
    logger.info(f"[SEARCH] Extracting params | user_text='{user_text[:50]}...' | root_text='{root_text[:50]}...'")
    search_params = extract_search_params(llm, user_text, root_text)
    logger.info(f"[SEARCH] Extracted params: {search_params}")
    provider = SEARCH_PROVIDERS.get(search_type)
    if not provider:
        logger.warning(f"[SEARCH] Unknown provider: {search_type}")
        return ""
    func = provider["func"]
    supported = provider.get("supports", [])
    kwargs = {k: v for k, v in search_params.items() if k in supported and v and str(v).lower() not in ["null", "none", ""]}
    query = search_params.get("query", "")
    logger.info(f"[SEARCH] Calling {search_type} | query='{query}' | kwargs={kwargs}")
    res = await func(query, **kwargs)
    if is_search_result_valid(res, search_type):
        logger.info(f"[SEARCH] Success | type={search_type} | result_len={len(str(res))}")
        return res
    logger.warning(f"[SEARCH] Invalid result | type={search_type}")
    return ""

def extract_search_params(llm, user_text, root_text):
    from prompts import QUERY_REFINE_SYSTEM
    from generator import _extract_text, clean_artifacts
    import json
    prompt = f"{QUERY_REFINE_SYSTEM.format(user_text=user_text, root_text=root_text)}"
    logger.debug(f"[SEARCH] LLM prompt for params: {prompt[:500]}...")
    try:
        response = llm(prompt, max_tokens=150, temperature=0.2)
        text = clean_artifacts(_extract_text(response))
        logger.debug(f"[SEARCH] LLM raw response: {text[:300]}...")
        params = json.loads(text)
        params["query"] = clean_artifacts(params.get("query", ""))
        logger.debug(f"[SEARCH] Parsed params: {params}")
        return params
    except Exception as e:
        logger.warning(f"[SEARCH] Failed to parse search params: {e} | fallback to user_text")
        return {"query": clean_artifacts(user_text), "time_range": None, "topic": None}

SEARCH_PROVIDERS = {
    "tavily": {"func": tavily_search, "supports": ["time_range", "topic"]},
    "chainbase": {"func": chainbase_search, "supports": []}
}
