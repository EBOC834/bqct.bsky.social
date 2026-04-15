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

async def post_record(client, bot_did, text, reply_obj=None):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created_at}
    if reply_obj:
        record["reply"] = reply_obj
    payload = {"repo": bot_did, "collection": "app.bsky.feed.post", "record": record}
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json=payload)
    r.raise_for_status()
    return r.json()

async def post_reply(client, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    if not root_uri or not parent_uri:
        raise ValueError("Missing required URI for reply")
    reply_obj = None
    if parent_cid:
        effective_root_uri = root_uri if root_cid else parent_uri
        effective_root_cid = root_cid if root_cid else parent_cid
        reply_obj = {"root": {"uri": effective_root_uri, "cid": effective_root_cid}, "parent": {"uri": parent_uri, "cid": parent_cid}}
    return await post_record(client, bot_did, text, reply_obj)

async def post_root(client, bot_did, text):
    return await post_record(client, bot_did, text)
