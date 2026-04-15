import os
import httpx
import re

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

SOURCE_SUFFIXES = {
    "tavily": "\n\nQwen | Tavily",
    "chainbase": "\n\nQwen | Chainbase"
}

def is_search_result_valid(search_results, search_type):
    if not search_results:
        return False
    if "Error" in search_results:
        return False
    if search_type == "chainbase" and "No specific trends" in search_results:
        return False
    if search_type == "tavily" and "Tavily API Key missing" in search_results:
        return False
    return True

async def tavily_search(query, time_range=None, topic=None, **kwargs):
    if not TAVILY_API_KEY:
        return "Tavily API Key missing."
    try:
        async with httpx.AsyncClient() as client:
            payload = {
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "include_raw_content": True,
                "max_results": 5
            }
            if time_range:
                payload["time_range"] = time_range
            if topic:
                payload["topic"] = topic
            r = await client.post("https://api.tavily.com/search", json=payload, timeout=30)
            if r.status_code == 200:
                data = r.json()
                from parser import parse_tavily_results
                return parse_tavily_results(data)
    except Exception as e:
        return f"Error: {e}"

async def chainbase_search(query, **kwargs):
    try:
        async with httpx.AsyncClient() as client:
            if query and len(query.strip()) > 3:
                url = "https://api.chainbase.com/tops/v1/tool/search-narrative-candidates"
                params = {"keyword": query, "language": "en", "limit": 3}
            else:
                url = "https://api.chainbase.com/tops/v1/tool/list-trending-topics"
                params = {"language": "en"}
            r = await client.get(url, headers={"x-api-key": "demo"}, params=params, timeout=30)
            if r.status_code == 200:
                data = r.json()
                from parser import parse_chainbase_results
                return parse_chainbase_results(data)
            return f"Chainbase API error: HTTP {r.status_code}"
    except Exception as e:
        return f"Error: {e}"

SEARCH_PROVIDERS = {
    "tavily": {"func": tavily_search, "supports": ["time_range", "topic"]},
    "chainbase": {"func": chainbase_search, "supports": []}
}
