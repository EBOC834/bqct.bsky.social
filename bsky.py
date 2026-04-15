import httpx
import datetime
import re

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
    def extract(node, depth=0):
        if not node:
            return
        p = node.get("post", {})
        record = p.get("record", {})
        text = record.get("text", "")
        embed = p.get("embed", {})
        embed_text = _extract_embed_full(embed)
        posts.append({
            "handle": p.get("author", {}).get("handle"),
            "text": text,
            "embed": embed_text,
            "cid": p.get("cid", ""),
            "is_root": (depth == 0)
        })
        for reply in node.get("replies", []):
            extract(reply, depth + 1)
    extract(data.get("thread", {}), depth=0)
    return posts

def _extract_embed_full(embed):
    """Extract ALL embed data: links, images, quotes, video"""
    parts = []
    if not embed:
        return ""
    
    embed_type = embed.get("$type", "")
    
    if embed_type == "app.bsky.embed.images":
        for i, img in enumerate(embed.get("images", []), 1):
            alt = img.get("alt", "").strip()
            if alt:
                parts.append(f"[Image {i}: {alt}]")
            else:
                parts.append(f"[Image {i}]")
    
    elif embed_type == "app.bsky.embed.external":
        ext = embed.get("external", {})
        title = ext.get("title", "").strip()
        desc = ext.get("description", "").strip()
        uri = ext.get("uri", "").strip()
        if title:
            parts.append(f"[Link: {title}]")
        if desc:
            parts.append(f"[Desc: {desc[:150]}]")
        if uri and not uri.startswith("https://bsky.app"):
            parts.append(f"[URL: {uri}]")
    
    elif embed_type == "app.bsky.embed.record":
        rec = embed.get("record", {})
        rec_type = rec.get("$type", "")
        if rec_type == "app.bsky.feed.post":
            val = rec.get("value", {})
            quote_text = val.get("text", "")[:150]
            quote_author = rec.get("author", {}).get("handle", "")
            if quote_text:
                parts.append(f"[Quote @{quote_author}: {quote_text}]")
        elif rec.get("title"):
            parts.append(f"[Record: {rec.get('title')}]")
    
    elif embed_type == "app.bsky.embed.video":
        video = embed.get("video", {})
        alt = video.get("alt", "").strip()
        if alt:
            parts.append(f"[Video: {alt}]")
        else:
            parts.append("[Video]")
    
    elif embed_type == "app.bsky.embed.recordWithMedia":
        media = embed.get("media", {})
        record = embed.get("record", {})
        parts.append(_extract_embed_full(media))
        parts.append(_extract_embed_full({"$type": "app.bsky.embed.record", "record": record}))
    
    return " ".join(p for p in parts if p)

def filter_and_select(posts, bot_handle, limit=8):
    if not posts:
        return []
    
    root_post = posts[0]
    other_posts = posts[1:]
    
    seen, valid = set(), [root_post]
    root_text = root_post.get("text", "").strip()
    if root_text:
        seen.add(root_text)
    
    for p in other_posts:
        t = p.get("text", "").strip()
        if len(t) >= 5 and t not in seen:
            seen.add(t)
            valid.append(p)
    
    if len(valid) > limit:
        return [valid[0]] + valid[-(limit-1):]
    return valid

def format_context(selected, bot_handle):
    lines = []
    for i, p in enumerate(selected):
        is_root = p.get("is_root", False)
        marker = " [ROOT]" if is_root else (" [BOT]" if p.get("handle") == bot_handle else "")
        embed = p.get("embed", "")
        text = p.get("text", "")
        line = f"@{p.get('handle', 'unknown')}{marker}: {text}"
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
    
    reply_obj = None
    if parent_cid:
        effective_root_uri = root_uri if root_cid else parent_uri
        effective_root_cid = root_cid if root_cid else parent_cid
        reply_obj = {
            "root": {"uri": effective_root_uri, "cid": effective_root_cid},
            "parent": {"uri": parent_uri, "cid": parent_cid}
        }
    
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
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json=payload)
    r.raise_for_status()
    return r.json()
