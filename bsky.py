import httpx
import datetime
import re
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)
BASE_URL = "https://bsky.social"

def get_client(): return httpx.AsyncClient(base_url=BASE_URL, timeout=30)

async def login(client, handle, password):
    r = await client.post("/xrpc/com.atproto.server.createSession", json={"identifier": handle, "password": password})
    r.raise_for_status()
    data = r.json()
    client.headers["Authorization"] = f"Bearer {data['accessJwt']}"
    return data["accessJwt"]

async def resolve_handle_to_did(client, handle: str) -> Optional[str]:
    try:
        r = await client.get("/xrpc/com.atproto.identity.resolveHandle", params={"handle": handle})
        if r.status_code == 200: return r.json().get("did")
    except: pass
    return None

def parse_uri(uri: str) -> Optional[Dict[str, str]]:
    if uri.startswith("at://"):
        parts = uri.split("/")
        if len(parts) >= 5: return {"did": parts[2], "collection": parts[3], "rkey": parts[4], "uri": uri}
    elif uri.startswith("https://bsky.app/profile/"):
        m = re.match(r"https://bsky\.app/profile/([^/]+)/post/([^/?#]+)", uri)
        if m: return {"handle": m.group(1), "rkey": m.group(2), "collection": "app.bsky.feed.post", "uri": uri}
    return None

async def normalize_uri(client, uri: str) -> Optional[str]:
    p = parse_uri(uri)
    if not p: return None
    if "did" in p: return f"at://{p['did']}/{p['collection']}/{p['rkey']}"
    if "handle" in p:
        did = await resolve_handle_to_did(client, p["handle"])
        if did: return f"at://{did}/{p['collection']}/{p['rkey']}"
    return None

async def get_record(client, uri: str):
    n = await normalize_uri(client, uri)
    if not n: return None
    parts = n.split("/")
    if len(parts) < 5: return None
    r = await client.get("/xrpc/com.atproto.repo.getRecord", params={"repo": parts[2], "collection": parts[3], "rkey": parts[4]})
    return r.json() if r.status_code == 200 else None

async def get_thread_raw(client, root_uri: str, token: str):
    r = await client.get(f"https://bsky.social/xrpc/app.bsky.feed.getPostThread?uri={root_uri}&depth=100", headers={"Authorization": f"Bearer {token}"}, timeout=60)
    return r.json() if r.status_code == 200 else None

def build_hashtag_facets(text: str, tags: list) -> list:
    facets = []
    for tag in tags:
        target = f"#{tag}"
        idx = text.find(target)
        if idx != -1:
            facets.append({"index": {"byteStart": len(text[:idx].encode("utf-8")), "byteEnd": len(text[:idx + len(target)].encode("utf-8"))}, "features": [{"$type": "app.bsky.richtext.facet#tag", "tag": tag}]})
    return facets

async def post_record(client, bot_did, text, reply_obj=None, facets=None):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created_at}
    if reply_obj: record["reply"] = reply_obj
    if facets: record["facets"] = facets
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": bot_did, "collection": "app.bsky.feed.post", "record": record})
    r.raise_for_status()
    return r.json()

async def post_reply(client, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    if not root_uri or not parent_uri: raise ValueError("Missing required URI for reply")
    reply_obj = None
    if parent_cid:
        reply_obj = {"root": {"uri": root_uri if root_cid else parent_uri, "cid": root_cid if root_cid else parent_cid}, "parent": {"uri": parent_uri, "cid": parent_cid}}
    return await post_record(client, bot_did, text, reply_obj)

async def post_root(client, bot_did, text, facets=None):
    return await post_record(client, bot_did, text, facets=facets)

async def like_post(client, bot_did, subject_uri, subject_cid):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {"$type": "app.bsky.feed.like", "subject": {"uri": subject_uri, "cid": subject_cid}, "createdAt": created_at}
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": bot_did, "collection": "app.bsky.feed.like", "record": record})
    r.raise_for_status()
    return r.json()
