import re
import httpx
from bs4 import BeautifulSoup
from trafilatura import extract as trafilatura_extract

async def extract_clean_url_content(url):
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            if r.status_code == 200:
                content = trafilatura_extract(r.text, include_tables=False, include_comments=False, output_format="txt")
                return content[:400].strip() if content else None
    except Exception:
        pass
    return None

async def extract_embed_full(embed):
    if not embed: return "", []
    parts, alts = [], []
    etype = embed.get("$type", "")
    if etype == "app.bsky.embed.images":
        for i, img in enumerate(embed.get("images", []), 1):
            alt = img.get("alt", "").strip()
            parts.append(f"[Image {i}: {alt}]" if alt else f"[Image {i}]")
            if alt: alts.append(f"Image {i}: {alt}")
    elif etype == "app.bsky.embed.external":
        ext = embed.get("external", {})
        parts.append(f"[Link: {ext.get('title', '')}]")
        if ext.get("description"): parts.append(f"[Desc: {ext['description'][:150]}]")
        if ext.get("uri") and not ext["uri"].startswith("https://bsky.app"): parts.append(f"[URL: {ext['uri']}]")
    elif etype == "app.bsky.embed.record":
        rec = embed.get("record", {})
        if rec.get("$type") == "app.bsky.feed.post":
            val = rec.get("value", {})
            if val.get("text"): parts.append(f"[Quote @{rec.get('author', {}).get('handle', '')}: {val['text'][:150]}]")
    return " ".join(p for p in parts if p), alts
