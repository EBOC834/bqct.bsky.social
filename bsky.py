import httpx
import datetime
import re
import logging
from typing import Dict, Optional

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
        p = uri.split("/")
        if len(p) >= 5: return {"did": p[2], "collection": p[3], "rkey": p[4], "uri": uri}
    elif uri.startswith("https://bsky.app/profile/"):
        m = re.match(r"https://bsky\.app/profile/([^/]+)/post/([^/?#]+)", uri)
        if m: return {"handle": m.group(1), "rkey": m.group(2), "collection": "app.bsky.feed.post", "uri": uri}
    return None

async def normalize_uri(client, uri: str) -> Optional[str]:
    p = parse_uri(uri)
    if not p: return None
    if "did" in p: return f"at://{p['did']}/{p['collection']}/{p['rkey']}"
    if "handle" in p:
        d = await resolve_handle_to_did(client, p["handle"])
        if d: return f"at://{d}/{p['collection']}/{p['rkey']}"
    return None

async def get_record(client, uri: str):
    n = await normalize_uri(client, uri)
    if not n: return None
    p = n.split("/")
    if len(p) < 5: return None
    r = await client.get("/xrpc/com.atproto.repo.getRecord", params={"repo": p[2], "collection": p[3], "rkey": p[4]})
    return r.json() if r.status_code == 200 else None

async def fetch_thread(client, target_uri: str):
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    r = await client.get(
        f"https://bsky.social/xrpc/app.bsky.feed.getPostThread?uri={target_uri}&depth=0&parentHeight=100",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30
    )
    if r.status_code != 200: return None
    chain = []
    current = r.json().get("thread", {})
    while current:
        post = current.get("post")
        if post: chain.append(post)
        current = current.get("parent")
    chain = list(reversed(chain))
    if not chain: return None
    root = chain[0]
    parent = next((p for p in chain if p.get("uri") == target_uri), root)
    return {
        "root_uri": root.get("uri"),
        "root_text": root.get("record", {}).get("text", ""),
        "root_cid": root.get("cid", ""),
        "parent_uri": target_uri,
        "parent_cid": parent.get("cid", "")
    }

async def post_record(client, bot_did, text, reply_obj=None):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created_at}
    if reply_obj: record["reply"] = reply_obj
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": bot_did, "collection": "app.bsky.feed.post", "record": record})
    r.raise_for_status()
    return r.json()

async def post_reply(client, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    if not root_uri or not parent_uri: raise ValueError("Missing URIs")
    eff_root_cid = root_cid or (await get_record(client, root_uri)).get("cid", "") if root_cid else ""
    eff_parent_cid = parent_cid or (await get_record(client, parent_uri)).get("cid", "") if parent_cid else ""
    reply_obj = {"root": {"uri": root_uri, "cid": eff_root_cid}, "parent": {"uri": parent_uri, "cid": eff_parent_cid}}
    return await post_record(client, bot_did, text, reply_obj)

async def post_root(client, bot_did, text):
    return await post_record(client, bot_did, text)

async def like_post(client, bot_did, uri, cid):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": bot_did, "collection": "app.bsky.feed.like", "record": {"$type": "app.bsky.feed.like", "subject": {"uri": uri, "cid": cid}, "createdAt": created_at}})
    r.raise_for_status()
    return r.json()
