import httpx
import json as json_lib
from core.config import TAVILY_API_KEY, SEARCH_TIMEOUT

async def tavily_search(query, time_range=None, topic=None):
    if not TAVILY_API_KEY: return "Error: Key missing"
    payload = {"query": query, "search_depth": "basic", "max_results": 3, "include_answer": True, "include_raw_content": "text"}
    if time_range: payload["time_range"] = str(time_range).lower()
    if topic: payload["topic"] = str(topic).lower()
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post("https://api.tavily.com/search", json=payload, headers={"Authorization": f"Bearer {TAVILY_API_KEY}", "Content-Type": "application/json"}, timeout=SEARCH_TIMEOUT)
            r.raise_for_status()
            return r.text
    except Exception as e:
        return f"Error: {e}"

async def chainbase_search(query=""):
    try:
        url = f"https://api.chainbase.com/tops/v1/tool/search-narrative-candidates?keyword={query}" if query else "https://api.chainbase.com/tops/v1/tool/list-trending-topics?language=en"
        async with httpx.AsyncClient() as c:
            r = await c.get(url, timeout=SEARCH_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            items = data.get("items", []) if isinstance(data, dict) else data
            items = [i for i in items if len(i.get("keyword", "")) > 2]
            items.sort(key=lambda x: x.get("score", 0), reverse=True)
            return items[:10]
    except Exception:
        return []

def format_search_result(raw, stype):
    if stype == "tavily":
        try:
            data = json_lib.loads(raw)
            parts = []
            if data.get("answer"): parts.append(f"AI Answer: {data['answer']}")
            for res in data.get("results", [])[:2]: parts.append(f"- {res.get('title', '')}: {res.get('content', '')[:150]}")
            return "\n".join(parts)
        except:
            return raw[:2000]
    if stype == "chainbase":
        lines = []
        for i, item in enumerate(raw[:6], 1):
            lines.append(f"{i}. {item['keyword']} (Score: {item['score']:.1f})\n{item.get('summary', '')[:200]}")
        return "\n".join(lines)
    return ""
