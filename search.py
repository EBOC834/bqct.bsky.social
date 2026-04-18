import os
import httpx
import logging
import re
from typing import List, Dict
from config import TAVILY_API_KEY, SEARCH_TIMEOUT

logger = logging.getLogger(__name__)

def clean_query(query: str) -> str:
    query = re.sub(r'\s*[!|/][tc]\s*', ' ', query)
    query = re.sub(r'\s+', ' ', query)
    return query.strip()

async def tavily_search(query: str, time_range: str = None, topic: str = None) -> str:
    if not TAVILY_API_KEY: return "Error: TAVILY_API_KEY not set"
    try:
        clean_q = clean_query(query)
        payload = {"query": clean_q, "api_key": TAVILY_API_KEY, "max_results": 5, "include_answer": True}
        if time_range: payload["time_range"] = time_range
        if topic: payload["topic"] = topic
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.tavily.com/search", json=payload, timeout=SEARCH_TIMEOUT)
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.error(f"Tavily search failed: {e}")
        return f"Error: {str(e)}"

async def chainbase_search(query: str) -> List[Dict]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://api.chainbase.com/tops/v1/tool/list-trending-topics?language=en",
                timeout=SEARCH_TIMEOUT
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("items", [])
            if not items: return []
            return items[:6]
    except Exception as e:
        logger.error(f"Chainbase search failed: {e}")
        return []

def is_search_result_valid(result, search_type: str) -> bool:
    if not result: return False
    if search_type == "tavily": return "Error" not in str(result) and len(str(result)) > 10
    if search_type == "chainbase": return isinstance(result, list) and len(result) > 0
    return True

SEARCH_PROVIDERS = {
    "tavily": {"func": tavily_search, "supports": ["time_range", "topic"]},
    "chainbase": {"func": chainbase_search, "supports": []}
}
