import httpx
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
    if len(parts) < 5:
        return None
    did, collection, rkey = parts[2], parts[3], parts[4]
    r = await client.get("/xrpc/com.atproto.repo.getRecord", params={"repo": did, "collection": collection, "rkey": rkey})
    return r.json() if r.status_code == 200 else None

async def get_thread_context(client, root_uri):
    parts = root_uri.split("/")
    if len(parts) < 5:
        return []
    did, collection, rkey = parts[2], parts[3], parts[4]
    r = await client.get("/xrpc/com.atproto.feed.getPostThread", params={"uri": root_uri, "depth": 60, "parentHeight": 50})
    if r.status_code != 200:
        return []
    data = r.json()
    posts = []
    def extract(node):
        if not node:
            return
        p = node.get("post", {})
        posts.append({
            "handle": p.get("author", {}).get("handle"),
            "text": p.get("record", {}).get("text", ""),
            "embed": p.get("embed"),
            "cid": p.get("cid", "")
        })
        for reply in node.get("replies", []):
            extract(reply)
    extract(data.get("thread", {}))
    return posts

def extract_embed_text(post):
    parts = []
    embed = post.get("embed", {})
    if embed.get("$type") == "app.bsky.embed.images":
        for img in embed.get("images", []):
            alt = img.get("alt", "").strip()
            if alt:
                parts.append(f"[Image: {alt}]")
    elif embed.get("$type") == "app.bsky.embed.external":
        ext = embed.get("external", {})
        if ext.get("title"):
            parts.append(f"[Link: {ext['title']}]")
        if ext.get("description"):
            parts.append(f"[Desc: {ext['description'][:100]}]")
    elif embed.get("$type") == "app.bsky.embed.record":
        rec = embed.get("record", {})
        if rec.get("text"):
            parts.append(f"[Quote: {rec['text'][:100]}]")
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
        if embed:
            line += f" {embed}"
        lines.append(line)
    return "\n".join(lines)

async def get_context_string(client, uri, bot_handle):
    posts = await get_thread_context(client, uri)
    selected = filter_and_select(posts, bot_handle)
    return format_context(selected, bot_handle)

async def post_reply(client, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    if not root_uri or not parent_uri:
        raise ValueError("Missing required URI for reply")
    
    print(f"[DEBUG] post_reply called:", flush=True)
    print(f"  - root_uri: {root_uri}", flush=True)
    print(f"  - root_cid: {root_cid[:20] if root_cid else 'EMPTY'}", flush=True)
    print(f"  - parent_uri: {parent_uri}", flush=True)
    print(f"  - parent_cid: {parent_cid[:20] if parent_cid else 'EMPTY'}", flush=True)
    
    # Build reply object: if root_cid missing but parent_cid exists, use parent as root too
    reply_obj = None
    if parent_cid:
        effective_root_uri = root_uri if root_cid else parent_uri
        effective_root_cid = root_cid if root_cid else parent_cid
        reply_obj = {
            "root": {"uri": effective_root_uri, "cid": effective_root_cid},
            "parent": {"uri": parent_uri, "cid": parent_cid}
        }
        print(f"[DEBUG] Reply object: root={effective_root_uri}, parent={parent_uri}", flush=True)
    
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": created_at
    }
    if reply_obj:
        record["reply"] = reply_obj
    
    payload = {
        "repo": bot_did,
        "collection": "app.bsky.feed.post",
        "record": record
    }
    
    print(f"[DEBUG] Posting to: /xrpc/com.atproto.repo.createRecord", flush=True)
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json=payload)
    print(f"[DEBUG] Post response: status={r.status_code}", flush=True)
    if r.status_code == 200:
        result = r.json()
        print(f"[DEBUG] Posted URI: {result.get('uri')}", flush=True)
        return result
    r.raise_for_status()
    return r.json()
