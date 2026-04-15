import httpx
import datetime
import re
from typing import List, Dict, Optional

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

def _is_sequential_thread_post(text: str) -> bool:
    return bool(re.match(r'^[\s"\']*(\d+)/(\d+)', text) or re.search(r'[\s"\'](\d+)/(\d+)[\s"\']', text[:50]))

def _extract_embed_full(embed: Optional[Dict]) -> tuple:
    parts, alts = [], []
    if not embed:
        return "", []
    
    embed_type = embed.get("$type", "")
    
    if embed_type == "app.bsky.embed.images":
        for i, img in enumerate(embed.get("images", []), 1):
            alt = img.get("alt", "").strip()
            if alt:
                parts.append(f"[Image {i}: {alt}]")
                alts.append(f"Image {i}: {alt}")
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
            alts.append(f"Video: {alt}")
        else:
            parts.append("[Video]")
    
    elif embed_type == "app.bsky.embed.recordWithMedia":
        media = embed.get("media", {})
        record = embed.get("record", {})
        media_text, media_alts = _extract_embed_full(media)
        record_text, _ = _extract_embed_full({"$type": "app.bsky.embed.record", "record": record})
        if media_text:
            parts.append(media_text)
            alts.extend(media_alts)
        if record_text:
            parts.append(record_text)
    
    return " ".join(p for p in parts if p), alts

async def get_thread_context(client, root_uri: str) -> List[Dict]:
    parts = root_uri.split("/")
    if len(parts) < 5:
        return []
    did, collection, rkey = parts[2], parts[3], parts[4]
    
    r = await client.get(
        "/xrpc/com.atproto.feed.getPostThread",
        params={"uri": root_uri, "depth": 100, "parentHeight": 50},
        timeout=60
    )
    if r.status_code != 200:
        return []
    
    data = r.json()
    all_nodes = []
    quoted_cache = {}
    
    async def collect_nodes(node, parent_uri=None, depth=0):
        if not node:
            return
        post = node.get("post", {})
        record = post.get("record", {})
        if not record:
            return
        
        node_uri = post.get("uri")
        author = post.get("author", {})
        did = author.get("did", "")
        handle = author.get("handle", did.split(":")[-1] if ":" in did else "unknown")
        txt = record.get("text", "")
        
        embed = record.get("embed")
        embed_text, alts = _extract_embed_full(embed)
        
        if embed and embed.get("$type") in ["app.bsky.embed.record", "app.bsky.embed.recordWithMedia"]:
            rec_ref = embed.get("record", {})
            if rec_ref and rec_ref.get("uri"):
                if rec_ref["uri"] not in quoted_cache:
                    emb_rec = await get_record(client, rec_ref["uri"])
                    if emb_rec and "value" in emb_rec:
                        quoted_cache[rec_ref["uri"]] = emb_rec["value"].get("text", "")[:200]
                emb_txt = quoted_cache.get(rec_ref["uri"], "")
                if emb_txt:
                    emb_author = rec_ref["uri"].split("/")[2] if "/" in rec_ref.get("uri", "") else "unknown"
                    txt = f"{txt}\n[🔁 @{emb_author}: {emb_txt}]"
        
        all_nodes.append({
            "uri": node_uri,
            "parent_uri": parent_uri,
            "did": did,
            "handle": handle,
            "text": txt,
            "embed": embed_text,
            "alts": alts,
            "is_root": (depth == 0),
            "is_sequential": _is_sequential_thread_post(txt)
        })
        
        for reply_node in node.get("replies", []):
            if isinstance(reply_node, dict):
                await collect_nodes(reply_node, node_uri, depth + 1)
    
    await collect_nodes(data.get("thread", {}), depth=0)
    return all_nodes

def _calculate_priority(node: Dict, bot_handle: str, owner_did: Optional[str] = None) -> int:
    if node.get("is_root"):
        return 10
    if node.get("is_sequential"):
        return 8
    if node["did"] == owner_did:
        return 6
    if node["handle"] == bot_handle:
        return 4
    return 2

def filter_and_select(posts: List[Dict], bot_handle: str, owner_did: Optional[str] = None, limit: int = 15) -> List[Dict]:
    if not posts:
        return []
    
    root_post = posts[0]
    other_posts = posts[1:]
    
    scored = [(p, _calculate_priority(p, bot_handle, owner_did)) for p in other_posts]
    scored.sort(key=lambda x: (-x[1], x[0]["uri"]))
    
    seen, valid = {root_post.get("text", "").strip()}, [root_post]
    for post, priority in scored:
        txt = post.get("text", "").strip()
        if priority >= 6 or (len(txt) >= 5 and txt not in seen):
            seen.add(txt)
            valid.append(post)
            if len(valid) >= limit:
                break
    
    valid.sort(key=lambda p: p["uri"].split("/")[-1])
    return valid

def format_context(selected: List[Dict], bot_handle: str) -> tuple:
    lines, all_alts = [], []
    for p in selected:
        is_root = p.get("is_root", False)
        marker = " [ROOT]" if is_root else (" [BOT]" if p.get("handle") == bot_handle else "")
        embed = p.get("embed", "")
        text = p.get("text", "")
        line = f"@{p.get('handle', 'unknown')}{marker}: {text}"
        if embed:
            line += f" {embed}"
        lines.append(line)
        all_alts.extend(p.get("alts", []))
    return "\n".join(lines), list(set(all_alts))

async def get_context_string(client, uri, bot_handle, owner_did=None):
    posts = await get_thread_context(client, uri)
    selected = filter_and_select(posts, bot_handle, owner_did)
    context_str, alts = format_context(selected, bot_handle)
    if alts:
        context_str += f"\n\n[Image/Video alts: {'; '.join(alts)}]"
    return context_str

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
