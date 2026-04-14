import os
import httpx
import re

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

SOURCE_SUFFIXES = {
    "tavily": "\n\nQwen | Tavily",
    "chainbase": "\n\nQwen | Chainbase",
    "bluesky": "\n\nQwen | Bluesky"
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
    if search_type == "bluesky" and ("Bluesky search error" in search_results or "No posts found" in search_results):
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
                "max_results": 3
            }
            if time_range:
                payload["time_range"] = time_range
            if topic:
                payload["topic"] = topic
            r = await client.post("https://api.tavily.com/search", json=payload, timeout=30)
            if r.status_code == 200:
                data = r.json()
                summary = f"AI Answer: {data.get('answer', '')}\n" if data.get('answer') else ""
                for res in data.get("results", []):
                    text = res.get("raw_content") or res.get("content", "")
                    pub_date = res.get("published_date", "")
                    if pub_date:
                        text = f"[{pub_date}] {text}"
                    summary += f"- {res.get('title', '')}: {text[:150]}...\n"
                return summary[:1000]
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
                summary = ""
                items = data.get("items", []) if isinstance(data.get("items"), list) else data if isinstance(data, list) else []
                for item in items[:3]:
                    keyword = item.get("keyword", "") or item.get("title", "")
                    text = item.get("summary", "")[:150]
                    if re.search(r'[a-zA-Z]', text):
                        summary += f"- {keyword}: {text}...\n"
                return summary[:1000] if summary else "No specific trends found."
    except Exception as e:
        return f"Error: {e}"

async def bluesky_search(query, **kwargs):
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
                params={"q": query, "sort": "top", "limit": 10},
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                summary = ""
                for post in data.get("posts", [])[:5]:
                    author = post.get("author", {}).get("handle", "unknown")
                    text = post.get("record", {}).get("text", "")[:150]
                    likes = post.get("likeCount", 0)
                    reposts = post.get("repostCount", 0)
                    summary += f"- @{author} ({likes}♥ {reposts}↻): {text}...\n"
                return summary[:1000] if summary else "No posts found."
            return "Bluesky search error."
    except Exception as e:
        return f"Error: {e}"

SEARCH_PROVIDERS = {
    "tavily": {"func": tavily_search, "supports": ["time_range", "topic"]},
    "chainbase": {"func": chainbase_search, "supports": []},
    "bluesky": {"func": bluesky_search, "supports": []}
}
