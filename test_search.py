import os
import sys
import httpx
import re
import json

def clean_query(q):
    return re.sub(r'\s*[!|/][tc]\s*', ' ', q).strip()

def test_tavily(query, time_range=None, topic=None):
    print(f"\n=== TAVILY TEST ===")
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        print("ERROR: TAVILY_API_KEY not set")
        return
    clean_q = clean_query(query)
    payload = {"query": clean_q, "search_depth": "basic", "max_results": 3, "include_answer": True, "include_raw_content": "text"}
    if time_range and time_range.lower() in ["day", "week", "month", "year"]:
        payload["time_range"] = time_range.lower()
    if topic and topic.lower() in ["news", "finance"]:
        payload["topic"] = topic.lower()
    print(f"Query: {clean_q}")
    print(f"Payload: {json.dumps(payload, indent=2)}")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        with httpx.Client() as client:
            r = client.post("https://api.tavily.com/search", json=payload, headers=headers, timeout=30)
            print(f"Status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                print(f"Answer: {data.get('answer', 'N/A')[:200]}...")
                print(f"Results count: {len(data.get('results', []))}")
            else:
                print(f"Error: {r.text[:500]}")
    except Exception as e:
        print(f"Exception: {e}")

def test_chainbase(query):
    print(f"\n=== CHAINBASE TEST ===")
    clean_q = clean_query(query)
    if clean_q:
        url = f"https://api.chainbase.com/tops/v1/tool/search-narrative-candidates?keyword={clean_q}"
        print(f"Search URL: {url}")
    else:
        url = "https://api.chainbase.com/tops/v1/tool/list-trending-topics?language=en"
        print(f"Trending URL: {url}")
    try:
        with httpx.Client() as client:
            r = client.get(url, timeout=30)
            print(f"Status: {r.status_code}")
            if r.status_code == 200:
                data = r.json()
                items = data.get("items", [])
                print(f"Results count: {len(items)}")
                for i, item in enumerate(items[:3]):
                    print(f"  [{i+1}] {item.get('keyword')} (score: {item.get('score')})")
            else:
                print(f"Error: {r.text[:500]}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    search_type = os.getenv("SEARCH_TYPE", "tavily")
    query = os.getenv("SEARCH_QUERY", "")
    time_range = os.getenv("TIME_RANGE", "")
    topic = os.getenv("TOPIC", "")
    if not query:
        print("ERROR: No query provided")
        sys.exit(1)
    print(f"Input Query: '{query}'")
    print(f"Search Type: {search_type}")
    print(f"Time Range: {time_range if time_range else 'None'}")
    print(f"Topic: {topic if topic else 'None'}")
    if search_type == "tavily":
        test_tavily(query, time_range, topic)
    elif search_type == "chainbase":
        test_chainbase(query)
    else:
        print(f"Unknown search type: {search_type}")
