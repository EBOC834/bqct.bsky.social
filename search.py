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
    if search_type == "bluesky" and ("Bluesky search error" in search_results or "No posts found" in search_results or "requires authentication" in search_results.lower() or "auth failed" in search_results.lower() or "credentials missing" in search_results.lower()):
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
    bot_handle = os.getenv("BOT_HANDLE")
    bot_password = os.getenv("BOT_PASSWORD")
    if not bot_handle or not bot_password:
        return "Bluesky search: credentials missing."
    try:
        async with httpx.AsyncClient() as auth_client:
            auth_r = await auth_client.post(
                "https://bsky.social/xrpc/com.atproto.server.createSession",
                json={"identifier": bot_handle, "password": bot_password},
                timeout=15
            )
            if auth_r.status_code != 200:
                return "Bluesky auth failed."
            token = auth_r.json().get('accessJwt')
            if not token:
                return "Bluesky: no token received."
        async with httpx.AsyncClient() as search_client:
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": "bluesky-bot/1.0"
            }
            r = await search_client.get(
                "https://bsky.social/xrpc/app.bsky.feed.searchPosts",
                params={"q": query, "sort": "top", "limit": 10},
                headers=headers,
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                posts = data.get("posts", [])
                if not posts:
                    return "No posts found."
                summary = ""
                for post in posts[:5]:
                    author = post.get("author", {}).get("handle", "unknown")
                    text = post.get("record", {}).get("text", "")[:150]
                    likes = post.get("likeCount", 0)
                    reposts = post.get("repostCount", 0)
                    summary += f"- @{author} ({likes}♥ {reposts}↻): {text}...\n"
                return summary[:1000]
            return f"Bluesky search error: HTTP {r.status_code}"
    except Exception as e:
        return f"Error: {e}"

SEARCH_PROVIDERS = {
    "tavily": {"func": tavily_search, "supports": ["time_range", "topic"]},
    "chainbase": {"func": chainbase_search, "supports": []},
    "bluesky": {"func": bluesky_search, "supports": []}
}
