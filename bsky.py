import httpx
import asyncio
import datetime

BASE_URL = "https://bsky.social"

def get_client():
    return httpx.AsyncClient(base_url=BASE_URL, timeout=30)

async def login(client, handle, password):
    r = await client.post("/xrpc/com.atproto.server.createSession", json={"identifier": handle, "password": password})
    r.raise_for_status()
    data = r.json()
    client.headers["Authorization"] = f"Bearer {data['accessJwt']}"
    return data["accessJwt"]

async def get_record(client, uri):
    parts = uri.split("/")
    did, collection, rkey = parts[2], parts[3], parts[4]
    r = await client.get("/xrpc/com.atproto.repo.getRecord", params={"repo": did, "collection": collection, "rkey": rkey})
    return r.json() if r.status_code == 200 else None

async def get_thread_context(client, root_uri):
    parts = root_uri.split("/")
    did, collection, rkey = parts[2], parts[3], parts[4]
    r = await client.get("/xrpc/com.atproto.feed.getPostThread", params={"uri": root_uri, "depth": 60, "parentHeight": 50})
    if r.status_code != 200: return []
    data = r.json()
    posts = []
    def extract(node):
        if not node: return
        p = node.get("post", {})
        posts.append({"handle": p.get("author", {}).get("handle"), "text": p.get("record", {}).get("text", ""), "embed": p.get("embed")})
        for reply in node.get("replies", []): extract(reply)
    extract(data.get("thread", {}))
    return posts

def extract_embed_text(post):
    parts = []
    embed = post.get("embed", {})
    if embed.get("$type") == "app.bsky.embed.images":
        for img in embed.get("images", []):
            alt = img.get("alt", "").strip()
            if alt: parts.append(f"[Image: {alt}]")
    elif embed.get("$type") == "app.bsky.embed.external":
        ext = embed.get("external", {})
        if ext.get("title"): parts.append(f"[Link: {ext['title']}]")
        if ext.get("description"): parts.append(f"[Desc: {ext['description'][:100]}]")
    elif embed.get("$type") == "app.bsky.embed.record":
        rec = embed.get("record", {})
        if rec.get("text"): parts.append(f"[Quote: {rec['text'][:100]}]")
    return " ".join(parts)

def filter_and_select(posts, bot_handle, limit=8):
    seen, valid = set(), []
    for p in posts:
        t = p.get("text", "").strip()
        if len(t) >= 5 and t not in seen:
            seen.add(t)
            valid.append(p)
    return valid if len(valid) <= limit else [valid[0]] + valid[-(limit-1):]

def format_context(selected, bot_handle):
    lines = []
    for p in selected:
        marker = " [BOT]" if p.get("handle") == bot_handle else ""
        embed = extract_embed_text(p)
        line = f"@{p.get('handle', 'unknown')}{marker}: {p.get('text', '')}"
        if embed: line += f" {embed}"
        lines.append(line)
    return "\n".join(lines)

async def post_reply(client, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    payload = {
        "repo": bot_did,
        "collection": "app.bsky.feed.post",
        "record": {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "reply": {"root": {"uri": root_uri, "cid": root_cid}, "parent": {"uri": parent_uri, "cid": parent_cid}}
        }
    }
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json=payload)
    return r.json()
