import os
import httpx

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

SOURCE_SUFFIXES = {
    "tavily": " with tavily",
    "chainbase": " with chainbase",
    "wiki": " with wikipedia"
}

async def tavily_search(query):
    if not TAVILY_API_KEY:
        return "Tavily API Key missing."
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": query, "search_depth": "basic", "include_answer": True, "max_results": 3},
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                summary = f"AI Answer: {data.get('answer', '')}\n" if data.get("answer") else ""
                for res in data.get("results", []):
                    summary += f"- {res.get('title', '')}: {res.get('content', '')[:150]}...\n"
                return summary[:1000]
    except Exception as e:
        return f"Error: {e}"

async def chainbase_search(query):
    try:
        async with httpx.AsyncClient() as client:
            url = "https://api.chainbase.com/tops/v1/tool/search-narrative-candidates"
            r = await client.get(
                url,
                headers={"x-api-key": "demo"},
                params={"keyword": query, "language": "en", "limit": 3},
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                summary = ""
                for item in data.get("items", [])[:3]:
                    keyword = item.get("keyword", "")
                    text = item.get("summary", "")[:150]
                    summary += f"- {keyword}: {text}...\n"
                return summary[:1000] if summary else "No specific trends found."
    except Exception as e:
        return f"Error: {e}"

async def wiki_search(query):
    try:
        async with httpx.AsyncClient() as client:
            search_url = "https://en.wikipedia.org/w/api.php"
            search_params = {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "format": "json",
                "srlimit": 1
            }
            r = await client.get(search_url, params=search_params, timeout=15)
            if r.status_code != 200:
                return "Wikipedia search error."
            
            data = r.json()
            results = data.get("query", {}).get("search", [])
            if not results:
                return f"No Wikipedia article found for '{query}'."
            
            title = results[0]["title"]
            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
            r = await client.get(summary_url, timeout=15)
            
            if r.status_code == 200:
                summary_data = r.json()
                extract = summary_data.get("extract", "")[:300]
                url = summary_data.get("content_urls", {}).get("desktop", {}).get("page", "")
                return f"{extract}... [More: {url}]"
            return "Could not fetch Wikipedia content."
    except Exception as e:
        return f"Error: {e}"

SEARCH_PROVIDERS = {
    "tavily": tavily_search,
    "chainbase": chainbase_search,
    "wiki": wiki_search
}
