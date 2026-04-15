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

async def resolve_handle_to_did(client, handle: str) -> Optional[str]:
    try:
        r = await client.get("/xrpc/com.atproto.identity.resolveHandle", params={"handle": handle})
        if r.status_code == 200:
            return r.json().get("did")
    except:
        pass
    return None

def parse_uri(uri: str) -> Optional[Dict[str, str]]:
    if uri.startswith("at://"):
        parts = uri.split("/")
        if len(parts) >= 5:
            return {"did": parts[2], "collection": parts[3], "rkey": parts[4], "uri": uri}
        return None
    elif uri.startswith("https://bsky.app/profile/"):
        match = re.match(r"https://bsky\.app/profile/([^/]+)/post/([^/?#]+)", uri)
        if match:
            return {"handle": match.group(1), "rkey": match.group(2), "collection": "app.bsky.feed.post", "uri": uri}
        return None
    return None

async def normalize_uri(client, uri: str) -> Optional[str]:
    parsed = parse_uri(uri)
    if not parsed:
        return None
    if "did" in parsed:
        return f"at://{parsed['did']}/{parsed['collection']}/{parsed['rkey']}"
    if "handle" in parsed:
        did = await resolve_handle_to_did(client, parsed["handle"])
        if did:
            return f"at://{did}/{parsed['collection']}/{parsed['rkey']}"
    return None

async def get_record(client, uri: str):
    normalized = await normalize_uri(client, uri)
    if not normalized:
        return None
    parts = normalized.split("/")
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

async def extract_link_metadata(url: str) -> Dict[str, str]:
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, 'html.parser')
                title = soup.find('meta', property='og:title')
                desc = soup.find('meta', property='og:description')
                return {
                    "title": title.get('content', '') if title else '',
                    "description": desc.get('content', '') if desc else ''
                }
    except:
        pass
    return {"title": "", "description": ""}

async def get_thread_context(client, root_uri: str) -> List[Dict]:
    rec = await get_record(client, root_uri)
    if not rec or "value" not in rec:
        return []
    
    post = rec.get("value", {})
    author = rec.get("author", {})
    
    txt = post.get("text", "")
    
    if "http" in txt:
        urls = re.findall(r'https?://[^\s]+', txt)
        if urls:
            lm = await extract_link_metadata(urls[0])
            if lm["title"]:
                txt = f"{txt}\n[Linked: {lm['title']}]"
    
    embed = post.get("embed")
    embed_text, alts = _extract_embed_full(embed)
    
    if alts:
        txt = f"{txt}\n[HINT from image alt: {'; '.join(alts)}]"
    
    return [{
        "uri": root_uri,
        "parent_uri": None,
        "did": author.get("did", ""),
        "handle": author.get("handle", ""),
        "text": txt,
        "embed": embed_text,
        "alts": alts,
        "is_root": True,
        "is_sequential": False
    }]

def filter_and_select(posts: List[Dict], bot_handle: str, owner_did: Optional[str] = None, limit: int = 10) -> List[Dict]:
    if not posts:
        return []
    
    root_post = posts[0]
    other_posts = posts[1:]
    
    other_posts.sort(key=lambda p: p["uri"].split("/")[-1], reverse=True)
    selected_others = other_posts[:limit]
    selected_others.sort(key=lambda p: p["uri"].split("/")[-1])
    
    return [root_post] + selected_others

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

async def post_root(client, bot_did, text):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": created_at
    }
    
    payload = {
        "repo": bot_did,
        "collection": "app.bsky.feed.post",
        "record": record
    }
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json=payload)
    r.raise_for_status()
    return r.json()
