import re
import httpx
from typing import List, Dict, Optional
from bs4 import BeautifulSoup
from trafilatura import extract as trafilatura_extract

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

async def _extract_link_metadata(url: str) -> Dict[str, str]:
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                t = soup.find('meta', property='og:title')
                d = soup.find('meta', property='og:description')
                return {"title": t.get('content', '') if t else '', "description": d.get('content', '') if d else ''}
    except:
        pass
    return {"title": "", "description": ""}

async def _extract_clean_url_content(url: str) -> Optional[str]:
    print(f"[PARSER] Fetching URL: {url}")
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
            print(f"[PARSER] URL status: {r.status_code}")
            if r.status_code == 200:
                content = trafilatura_extract(r.text, include_tables=False, include_comments=False, output_format="txt")
                if content:
                    cleaned = content[:400].strip()
                    print(f"[PARSER] Extracted content ({len(cleaned)} chars): {cleaned[:150]}...")
                    return cleaned
                else:
                    print(f"[PARSER] trafilatura returned empty content")
            else:
                print(f"[PARSER] Failed to fetch URL, status {r.status_code}")
    except Exception as e:
        print(f"[PARSER] Error fetching URL: {e}")
    return None

def _extract_urls_from_text(text: str) -> List[str]:
    return re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)

def parse_bluesky_post(raw_record: Dict) -> Dict:
    if not raw_record or "value" not in raw_record:
        return {}
    post = raw_record.get("value", {})
    author = raw_record.get("author", {})
    txt = post.get("text", "")
    embed = post.get("embed")
    embed_text, alts = _extract_embed_full(embed)
    link_hints = []
    if "http" in txt:
        urls = re.findall(r'https?://[^\s]+', txt)
        if urls:
            lm = _extract_link_metadata_sync(urls[0])
            if lm.get("title"):
                link_hints.append(f"[Linked: {lm['title']}]")
                print(f"[PARSER] Added link metadata: {lm['title']}")
    return {
        "uri": raw_record.get("uri", ""),
        "did": author.get("did", ""),
        "handle": author.get("handle", ""),
        "text": txt,
        "embed": embed_text,
        "alts": alts,
        "link_hints": link_hints,
        "is_root": False,
        "is_sequential": _is_sequential_thread_post(txt),
        "cid": raw_record.get("cid", "")
    }

def _extract_link_metadata_sync(url: str) -> Dict[str, str]:
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            if r.status == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.read(), 'html.parser')
                t = soup.find('meta', property='og:title')
                d = soup.find('meta', property='og:description')
                return {"title": t.get('content', '') if t else '', "description": d.get('content', '') if d else ''}
    except:
        pass
    return {"title": "", "description": ""}

async def parse_thread(thread_data: Dict, token: str, client) -> List[Dict]:
    all_nodes = []
    quoted_cache = {}
    link_cache = {}

    async def collect_nodes(node, parent_uri=None):
        if not node:
            return
        if node.get("$type") in ["app.bsky.feed.defs#notFoundPost", "app.bsky.feed.defs#blockedPost"]:
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
        alts = []
        link_hints = []

        if embed and isinstance(embed, dict):
            etype = embed.get("$type", "")
            if etype == "app.bsky.embed.record":
                rec_ref = embed.get("record", {})
                if rec_ref and rec_ref.get("uri"):
                    if rec_ref["uri"] not in quoted_cache:
                        parts = rec_ref["uri"].split("/")
                        if len(parts) >= 5:
                            q = await client.get(
                                f"https://bsky.social/xrpc/com.atproto.repo.getRecord",
                                params={"repo": parts[2], "collection": parts[3], "rkey": parts[4]},
                                headers={"Authorization": f"Bearer {token}"}
                            )
                            if q.status_code == 200:
                                quoted_cache[rec_ref["uri"]] = q.json().get("value", {}).get("text", "")[:200]
                    if rec_ref["uri"] in quoted_cache:
                        q_author = rec_ref["uri"].split("/")[2]
                        txt = f"{txt}\n[Quote @{q_author}: {quoted_cache[rec_ref['uri']]}]"
            elif etype == "app.bsky.embed.recordWithMedia":
                rec_ref = embed.get("record", {})
                media = embed.get("media", {})
                if rec_ref and rec_ref.get("uri"):
                    if rec_ref["uri"] not in quoted_cache:
                        parts = rec_ref["uri"].split("/")
                        if len(parts) >= 5:
                            q = await client.get(
                                f"https://bsky.social/xrpc/com.atproto.repo.getRecord",
                                params={"repo": parts[2], "collection": parts[3], "rkey": parts[4]},
                                headers={"Authorization": f"Bearer {token}"}
                            )
                            if q.status_code == 200:
                                quoted_cache[rec_ref["uri"]] = q.json().get("value", {}).get("text", "")[:200]
                    if rec_ref["uri"] in quoted_cache:
                        q_author = rec_ref["uri"].split("/")[2]
                        txt = f"{txt}\n[Quote @{q_author}: {quoted_cache[rec_ref['uri']]}]"
                if media and media.get("$type") == "app.bsky.embed.images":
                    for img in media.get("images", []):
                        if isinstance(img, dict) and img.get("alt"):
                            alts.append(f"@{handle} image: {img['alt']}")
            elif etype == "app.bsky.embed.images":
                for img in embed.get("images", []):
                    if isinstance(img, dict) and img.get("alt"):
                        alts.append(f"@{handle} image: {img['alt']}")
            elif etype == "app.bsky.embed.external":
                ext = embed.get("external", {})
                title = ext.get("title", "").strip()
                desc = ext.get("description", "").strip()
                uri = ext.get("uri", "").strip()
                if title:
                    link_hints.append(f"[Embed Link: {title}]")
                if desc:
                    link_hints.append(f"[Desc: {desc[:150]}]")
                if uri and uri not in link_cache:
                    clean = await _extract_clean_url_content(uri)
                    if clean:
                        link_cache[uri] = clean
                        link_hints.append(f"[Page content: {clean}]")
                        print(f"[PARSER] Added page content for embed link: {uri[:50]}...")
                    else:
                        link_cache[uri] = link_cache.get(uri, "[Page fetch failed]")
                        print(f"[PARSER] Could not extract content from embed link: {uri}")

        for url in _extract_urls_from_text(txt):
            if url not in link_cache:
                clean = await _extract_clean_url_content(url)
                if clean:
                    link_cache[url] = clean
                    link_hints.append(f"[Linked page: {clean}]")
                    print(f"[PARSER] Added linked page content: {url[:50]}...")
                else:
                    lm = await _extract_link_metadata(url)
                    if lm.get("title"):
                        link_hints.append(f"[Linked: {lm['title']}]")
                        print(f"[PARSER] Added link metadata fallback: {lm['title']}")
                link_cache[url] = link_cache.get(url, "[Fetch failed]")

        all_nodes.append({
            "uri": node_uri,
            "parent_uri": parent_uri,
            "did": did,
            "handle": handle,
            "text": txt,
            "alts": alts,
            "link_hints": link_hints,
            "is_root": (parent_uri is None)
        })

        for reply_node in node.get("replies", []):
            if isinstance(reply_node, dict):
                await collect_nodes(reply_node, node_uri)

    await collect_nodes(thread_data.get("thread", {}))
    return all_nodes

def parse_tavily_results(raw_data: Dict) -> str:
    summary = ""
    if raw_data.get("answer"):
        summary = f"AI Answer: {raw_data['answer']}\n"
    for res in raw_data.get("results", []):
        text = res.get("raw_content") or res.get("content", "")
        pub_date = res.get("published_date", "")
        if pub_date:
            text = f"[{pub_date}] {text}"
        summary += f"- {res.get('title', '')}: {text[:150]}...\n"
    return summary[:2000]

def parse_chainbase_results(raw_data: Dict) -> str:
    items = raw_data.get("items")
    if not items or not isinstance(items, list):
        return "No specific trends found."
    summary = ""
    for item in items[:3]:
        keyword = item.get("keyword", "")
        summary_text = item.get("summary", "")[:150]
        rank = item.get("rank_status", "")
        score = item.get("score", 0)
        if re.search(r'[a-zA-Z]', summary_text):
            summary += f"- {keyword} [{rank}, score:{score}]: {summary_text}...\n"
    return summary[:2000] if summary else "No specific trends found."
