import re
import httpx
from typing import List, Dict, Optional

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
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
            if r.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, 'html.parser')
                title = soup.find('meta', property='og:title')
                desc = soup.find('meta', property='og:description')
                site_name = soup.find('meta', property='og:site_name')
                result = {
                    "title": title.get('content', '').strip() if title else '',
                    "description": desc.get('content', '').strip() if desc else '',
                    "site_name": site_name.get('content', '').strip() if site_name else ''
                }
                if not result["title"]:
                    h1 = soup.find('h1')
                    if h1:
                        result["title"] = h1.get_text().strip()[:150]
                return result
    except:
        pass
    return {"title": "", "description": "", "site_name": ""}

def parse_bluesky_post(raw_record: Dict) -> Dict:
    if not raw_record or "value" not in raw_record:
        return {}
    post = raw_record.get("value", {})
    author = raw_record.get("author", {})
    txt = post.get("text", "")
    embed = post.get("embed")
    embed_text, alts = _extract_embed_full(embed)
    if "http" in txt:
        urls = re.findall(r'https?://[^\s]+', txt)
        if urls:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop:
                lm = asyncio.run(extract_link_metadata(urls[0]))
            else:
                import asyncio
                lm = asyncio.run(extract_link_metadata(urls[0]))
            parts = []
            if lm.get("site_name"):
                parts.append(f"[{lm['site_name']}]")
            if lm.get("title"):
                parts.append(f"{lm['title']}")
            if lm.get("description"):
                parts.append(f"{lm['description'][:150]}")
            if parts:
                txt = f"{txt}\n[Linked: {' '.join(parts)}]"
    if alts:
        txt = f"{txt}\n[HINT from image alt: {'; '.join(alts)}]"
    return {
        "uri": raw_record.get("uri", ""),
        "did": author.get("did", ""),
        "handle": author.get("handle", ""),
        "text": txt,
        "embed": embed_text,
        "alts": alts,
        "is_root": False,
        "is_sequential": _is_sequential_thread_post(txt),
        "cid": raw_record.get("cid", "")
    }

def parse_bluesky_thread(raw_thread: Dict, root_uri: str) -> List[Dict]:
    posts = []
    def collect(node, depth=0):
        if not node:
            return
        node_type = node.get("$type", "")
        if node_type in ["app.bsky.feed.defs#notFoundPost", "app.bsky.feed.defs#blockedPost"]:
            return
        post = node.get("post", {})
        if not post:
            return
        parsed = parse_bluesky_post(post)
        if parsed:
            parsed["is_root"] = (depth == 0)
            posts.append(parsed)
        replies = node.get("replies", [])
        if isinstance(replies, list):
            for reply in replies:
                if isinstance(reply, dict):
                    collect(reply, depth + 1)
    collect(raw_thread.get("thread", {}), depth=0)
    return posts

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
