import httpx
from bs4 import BeautifulSoup
from datetime import datetime, timezone

BSERVICE = "https://bsky.social"

def get_client():
    return httpx.AsyncClient()

async def login(client, handle, password):
    r = await client.post(f"{BSERVICE}/xrpc/com.atproto.server.createSession", json={"identifier": handle, "password": password}, timeout=30)
    r.raise_for_status()
    return r.json()["accessJwt"]

async def get_record(client, token, uri):
    parts = uri.split("/")
    if len(parts) < 5:
        return None
    repo, collection, rkey = parts[2:5]
    r = await client.get(f"{BSERVICE}/xrpc/com.atproto.repo.getRecord", headers={"Authorization": f"Bearer {token}"}, params={"repo": repo, "collection": collection, "rkey": rkey}, timeout=30)
    return r.json() if r.status_code == 200 else None

async def get_thread_context(client, token, root_uri):
    r = await client.get(f"{BSERVICE}/xrpc/app.bsky.feed.getPostThread", headers={"Authorization": f"Bearer {token}"}, params={"uri": root_uri, "depth": 5, "parentHeight": 1}, timeout=30)
    if r.status_code != 200:
        return []
    thread = r.json().get("thread", {})
    posts = []
    stack = [thread]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if "post" in node:
            rec = node["post"].get("record", {})
            posts.append({"handle": node["post"].get("author", {}).get("handle", "unknown"), "text": rec.get("text", "")})
            for key in ("replies", "parent"):
                if key in node and isinstance(node[key], dict):
                    stack.append(node[key])
    return posts[-5:] if len(posts) > 5 else posts

async def extract_link_metadata(url):
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            soup = BeautifulSoup(r.text, "html.parser")
            title_tag = soup.find("title")
            return {"title": title_tag.text.strip() if title_tag else ""}
    except Exception:
        return {"title": ""}

async def post_reply(client, token, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": now,
        "reply": {"root": {"uri": root_uri, "cid": root_cid}, "parent": {"uri": parent_uri, "cid": parent_cid}}
    }
    payload = {"repo": bot_did, "collection": "app.bsky.feed.post", "record": record}
    r = await client.post(f"{BSERVICE}/xrpc/com.atproto.repo.createRecord", headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()
