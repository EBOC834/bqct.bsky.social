import httpx
import json
import os
from datetime import datetime
from bs4 import BeautifulSoup

def get_client():
    return httpx.AsyncClient()

async def login(client, handle, password):
    r = await client.post(
        "https://bsky.social/xrpc/com.atproto.server.createSession",
        json={"identifier": handle, "password": password},
        timeout=30
    )
    r.raise_for_status()
    return r.json()["accessJwt"]

async def get_record(client, token, uri):
    try:
        parts = uri.split("/")
        if len(parts) < 5: return None
        repo = parts[2]
        collection = parts[3]
        rkey = parts[4]
        url = f"https://bsky.social/xrpc/com.atproto.repo.getRecord?repo={repo}&collection={collection}&rkey={rkey}"
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception as e:
        print(f"Error fetching record: {e}")
        return None

async def get_thread_context(client, token, root_uri):
    """Fetches the full thread context for a given root URI."""
    try:
        url = f"https://bsky.social/xrpc/app.bsky.feed.getPostThread?uri={root_uri}&depth=100"
        r = await client.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if r.status_code != 200:
            return []
        
        data = r.json()
        thread = data.get("thread", {})
        posts = []
        
        def collect_posts(node):
            post = node.get("post", {})
            if not post: return
            record = post.get("record", {})
            author = post.get("author", {})
            handle = author.get("handle", "unknown")
            text = record.get("text", "")
            
            # Extract alt text from images
            alts = []
            embed = record.get("embed")
            if embed and isinstance(embed, dict):
                if embed.get("$type") == "app.bsky.embed.images":
                    for img in embed.get("images", []):
                        if img.get("alt"): alts.append(img["alt"])
                elif embed.get("$type") == "app.bsky.embed.external":
                    ext = embed.get("external", {})
                    if ext.get("alt"): alts.append(ext["alt"])

            posts.append({
                "handle": handle,
                "text": text,
                "alts": alts,
                "uri": post.get("uri"),
                "cid": post.get("cid")
            })
            
            for reply in node.get("replies", []):
                collect_posts(reply)

        collect_posts(thread)
        return posts
    except Exception as e:
        print(f"Error fetching thread: {e}")
        return []

async def fetch_notifications(client, token, limit=20):
    r = await client.get(
        "https://bsky.social/xrpc/app.bsky.notification.listNotifications",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": limit},
        timeout=30
    )
    r.raise_for_status()
    return r.json().get("notifications", [])

async def post_reply(client, token, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    payload = {
        "repo": bot_did,
        "collection": "app.bsky.feed.post",
        "record": {
            "$type": "app.bsky.feed.post",
            "text": text,
            "reply": {
                "root": {"uri": root_uri, "cid": root_cid},
                "parent": {"uri": parent_uri, "cid": parent_cid}
            },
            "createdAt": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
        }
    }
    await client.post(
        "https://bsky.social/xrpc/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=30
    )

async def extract_link_metadata(url):
    try:
        async with httpx.AsyncClient() as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
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
