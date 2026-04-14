import os
import httpx
import re

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

SOURCE_SUFFIXES = {
    "tavily": "\n\nQwen | Tavily",
    "chainbase": "\n\nQwen | Chainbase",
    "wiki": "\n\nQwen | Wiki"
}

def is_english(text):
    return bool(re.search(r'[a-zA-Z]', text))

def is_search_result_valid(search_results, search_type):
    if not search_results: return False
    if "Error" in search_results: return False
    if search_type == "chainbase" and "No specific trends" in search_results: return False
    if search_type == "wiki" and ("No Wikipedia article" in search_results or "Could not fetch" in search_results): return False
    if search_type == "tavily" and "Tavily API Key missing" in search_results: return False
    return True

async def tavily_search(query):
    if not TAVILY_API_KEY: return "Tavily API Key missing."
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post("https://api.tavily.com/search", json={"api_key": TAVILY_API_KEY, "query": query, "search_depth": "basic", "include_answer": True, "max_results": 3}, timeout=30)
            if r.status_code == 200:
                data = r.json()
                summary = f"AI Answer: {data.get('answer', '')}\n" if data.get("answer") else ""
                for res in data.get("results", []):
                    summary += f"- {res.get('title', '')}: {res.get('content', '')[:150]}...\n"
                return summary[:1000]
    except Exception as e: return f"Error: {e}"

async def chainbase_search(query):
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
                summary = ""
                items = data.get("items", []) if isinstance(data.get("items"), list) else data if isinstance(data, list) else []
                for item in items[:3]:
                    keyword = item.get("keyword", "") or item.get("title", "")
                    text = item.get("summary", "")[:150]
                    if is_english(text):
                        summary += f"- {keyword}: {text}...\n"
                return summary[:1000] if summary else "No specific trends found."
    except Exception as e: return f"Error: {e}"

async def wiki_search(query):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get("https://en.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": query, "format": "json", "srlimit": 1}, timeout=15)
            if r.status_code != 200: return "Wikipedia search error."
            data = r.json()
            results = data.get("query", {}).get("search", [])
            if not results: return f"No Wikipedia article found for '{query}'."
            title = results[0]["title"]
            r2 = await client.get(f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}", timeout=15)
            if r2.status_code == 200:
                s_data = r2.json()
                extract = s_data.get("extract", "")[:300]
                url = s_data.get("content_urls", {}).get("desktop", {}).get("page", "")
                return f"{extract}... [More: {url}]"
            return "Could not fetch Wikipedia content."
    except Exception as e: return f"Error: {e}"

SEARCH_PROVIDERS = {"tavily": tavily_search, "chainbase": chainbase_search, "wiki": wiki_search}
