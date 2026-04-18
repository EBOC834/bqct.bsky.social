import re
import httpx
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from trafilatura import extract as trafilatura_extract

logger = logging.getLogger(__name__)

def _extract_embed_full(embed: Optional[Dict]) -> tuple:
    parts, alts = [], []
    if not embed: return "", []
    etype = embed.get("$type", "")
    if etype == "app.bsky.embed.images":
        for i, img in enumerate(embed.get("images", []), 1):
            alt = img.get("alt", "").strip()
            parts.append(f"[Image {i}: {alt}]" if alt else f"[Image {i}]")
    elif etype == "app.bsky.embed.external":
        ext = embed.get("external", {})
        if ext.get("title"): parts.append(f"[Link: {ext['title']}]")
        if ext.get("description"): parts.append(f"[Desc: {ext['description'][:150]}]")
        if ext.get("uri") and not ext["uri"].startswith("https://bsky.app"): parts.append(f"[URL: {ext['uri']}]")
    elif etype == "app.bsky.embed.record":
        rec = embed.get("record", {})
        if rec.get("$type") == "app.bsky.feed.post":
            val = rec.get("value", {})
            if val.get("text"): parts.append(f"[Quote @{rec.get('author', {}).get('handle', '')}: {val['text'][:150]}]")
    elif etype == "app.bsky.embed.recordWithMedia":
        mt, _ = _extract_embed_full(embed.get("media", {}))
        rt, _ = _extract_embed_full({"$type": "app.bsky.embed.record", "record": embed.get("record", {})})
        if mt: parts.append(mt)
        if rt: parts.append(rt)
    return " ".join(p for p in parts if p), alts

async def _extract_link_metadata(url: str) -> Dict[str, str]:
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                t = soup.find('meta', property='og:title')
                d = soup.find('meta', property='og:description')
                return {"title": t.get('content', '') if t else '', "description": d.get('content', '') if d else ''}
    except: pass
    return {"title": "", "description": ""}

async def _extract_clean_url_content(url: str) -> Optional[str]:
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if r.status_code == 200:
                content = trafilatura_extract(r.text, include_tables=False, include_comments=False, output_format="txt")
                if content: return content[:400].strip()
    except: pass
    return None

async def parse_thread(thread_data: Dict, token: str, client) -> List[Dict]:
    out = []
    q_cache, l_cache = {}, {}
    def walk(node, pid=None):
        if not node or node.get("$type") in ["app.bsky.feed.defs#notFoundPost", "app.bsky.feed.defs#blockedPost"]: return
        post = node.get("post", {})
        rec = post.get("record", {})
        if not rec: return
        uri = post.get("uri")
        auth = post.get("author", {})
        did = auth.get("did", "")
        handle = auth.get("handle", did.split(":")[-1] if ":" in did else "unknown")
        txt = rec.get("text", "")
        embed = rec.get("embed")
        if embed and isinstance(embed, dict):
            et = embed.get("$type", "")
            if et in ["app.bsky.embed.record", "app.bsky.embed.recordWithMedia"]:
                ref = embed.get("record", {})
                if ref.get("uri") and ref["uri"] not in q_cache:
                    p = ref["uri"].split("/")
                    if len(p) >= 5:
                        try:
                            qr = await client.get("https://bsky.social/xrpc/com.atproto.repo.getRecord", params={"repo": p[2], "collection": p[3], "rkey": p[4]}, headers={"Authorization": f"Bearer {token}"})
                            if qr.status_code == 200: q_cache[ref["uri"]] = qr.json().get("value", {}).get("text", "")[:200]
                        except: pass
                if ref.get("uri") in q_cache:
                    txt = f"{txt}\n[Quote @{ref['uri'].split('/')[2]}: {q_cache[ref['uri']]}]"
            elif et == "app.bsky.embed.external":
                ext = embed.get("external", {})
                if ext.get("title"): pass
                if ext.get("uri") and ext["uri"] not in l_cache:
                    clean = await _extract_clean_url_content(ext["uri"])
                    l_cache[ext["uri"]] = clean or "[Fetch failed]"
        for url in re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', txt):
            if url not in l_cache:
                clean = await _extract_clean_url_content(url)
                l_cache[url] = clean or "[Fetch failed]"
        out.append({"uri": uri, "parent_uri": pid, "did": did, "handle": handle, "text": txt, "is_root": pid is None})
        for r in node.get("replies", []):
            if isinstance(r, dict): walk(r, uri)
    walk(thread_data.get("thread", {}))
    return out
