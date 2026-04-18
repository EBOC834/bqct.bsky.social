import re
import httpx
import logging
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from trafilatura import extract as trafilatura_extract

logger = logging.getLogger(__name__)

def _extract_embed_full(embed: Optional[Dict]) -> tuple:
    parts, alts = [], []
    if not embed:
        return "", []
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
    elif etype == "app.bsky.embed.video":
        alt = embed.get("video", {}).get("alt", "").strip()
        parts.append(f"[Video: {alt}]" if alt else "[Video]")
    elif etype == "app.bsky.embed.recordWithMedia":
        mt, _ = _extract_embed_full(embed.get("media", {}))
        rt, _ = _extract_embed_full({"$type": "app.bsky.embed.record", "record": embed.get("record", {})})
        if mt: parts.append(mt)
        if rt: parts.append(rt)
    return " ".join(p for p in parts if p), alts

async def _extract_link_metadata(url):
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                return {"title": soup.find('meta', property='og:title')?.get('content', '') or '', "description": soup.find('meta', property='og:description')?.get('content', '') or ''}
    except:
        pass
    return {"title": "", "description": ""}

async def _extract_clean_url_content(url):
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if r.status_code == 200:
                content = trafilatura_extract(r.text, include_tables=False, include_comments=False, output_format="txt")
                if content:
                    return content[:400].strip()
    except:
        pass
    return None

async def _parse_nodes(node, parent_uri, client, token, q_cache, l_cache, out):
    if not node or node.get("$type") in ["app.bsky.feed.defs#notFoundPost", "app.bsky.feed.defs#blockedPost"]:
        return
    post = node.get("post", {})
    rec = post.get("record", {})
    if not rec:
        return
    uri = post.get("uri")
    author = post.get("author", {})
    did = author.get("did", "")
    handle = author.get("handle", did.split(":")[-1] if ":" in did else "unknown")
    txt = rec.get("text", "")
    embed = rec.get("embed")
    alts, hints = [], []
    if embed and isinstance(embed, dict):
        etype = embed.get("$type", "")
        if etype in ["app.bsky.embed.record", "app.bsky.embed.recordWithMedia"]:
            ref = embed.get("record", {})
            if ref.get("uri") and ref["uri"] not in q_cache:
                parts = ref["uri"].split("/")
                if len(parts) >= 5:
                    try:
                        qr = await client.get("https://bsky.social/xrpc/com.atproto.repo.getRecord", params={"repo": parts[2], "collection": parts[3], "rkey": parts[4]}, headers={"Authorization": f"Bearer {token}"})
                        if qr.status_code == 200: q_cache[ref["uri"]] = qr.json().get("value", {}).get("text", "")[:200]
                    except:
                        pass
            if ref.get("uri") in q_cache:
                txt = f"{txt}\n[Quote @{ref['uri'].split('/')[2]}: {q_cache[ref['uri']]}]"
            if etype == "app.bsky.embed.recordWithMedia":
                for img in embed.get("media", {}).get("images", []):
                    if img.get("alt"): alts.append(f"@{handle} image: {img['alt']}")
        elif etype == "app.bsky.embed.images":
            for img in embed.get("images", []):
                if img.get("alt"): alts.append(f"@{handle} image: {img['alt']}")
        elif etype == "app.bsky.embed.external":
            ext = embed.get("external", {})
            if ext.get("title"): hints.append(f"[Embed Link: {ext['title']}]")
            if ext.get("description"): hints.append(f"[Desc: {ext['description'][:150]}]")
            if ext.get("uri") and ext["uri"] not in l_cache:
                clean = await _extract_clean_url_content(ext["uri"])
                l_cache[ext["uri"]] = clean or "[Fetch failed]"
                if clean: hints.append(f"[Page content: {clean}]")
    for url in re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', txt):
        if url not in l_cache:
            clean = await _extract_clean_url_content(url)
            l_cache[url] = clean or "[Fetch failed]"
            if clean: hints.append(f"[Linked page: {clean}]")
            elif lm := await _extract_link_metadata(url):
                if lm.get("title"): hints.append(f"[Linked: {lm['title']}]")
    out.append({"uri": uri, "parent_uri": parent_uri, "did": did, "handle": handle, "text": txt, "alts": alts, "link_hints": hints, "is_root": parent_uri is None})
    for r in node.get("replies", []):
        if isinstance(r, dict): await _parse_nodes(r, uri, client, token, q_cache, l_cache, out)

async def parse_thread(thread_data: Dict, token: str, client) -> List[Dict]:
    out = []
    await _parse_nodes(thread_data.get("thread", {}), None, client, token, {}, {}, out)
    return out

def parse_tavily_results(raw_data: Dict) -> str:
    s = f"AI Answer: {raw_data.get('answer', '')}\n" if raw_data.get("answer") else ""
    for res in raw_data.get("results", []):
        t = res.get("raw_content") or res.get("content", "")
        if res.get("published_date"): t = f"[{res['published_date']}] {t}"
        s += f"- {res.get('title', '')}: {t[:150]}...\n"
    return s[:2000]

def parse_chainbase_results(raw_data: Dict) -> str:
    items = raw_data.get("items")
    if not items or not isinstance(items, list): return ""
    item = items[0]
    txt = item.get("summary", "").strip()
    if not txt: return ""
    if len(txt) > 220: txt = txt[:txt.rfind('.', 0, 220)+1] if txt.rfind('.', 0, 220) > 0 else txt[:220].rsplit(' ', 1)[0] + '.'
    elif not txt.endswith(('.', '!', '?')): txt += '.'
    return f"- {item.get('keyword', '')} [score:{int(item.get('score', 0))}]: {txt}"
