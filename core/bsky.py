import httpx
import datetime
import re
from typing import Dict, Optional

BASE_URL = "https://bsky.social"

def get_client():
    return httpx.AsyncClient(base_url=BASE_URL, timeout=30)

async def login(client, handle, password):
    r = await client.post("/xrpc/com.atproto.server.createSession", json={"identifier": handle, "password": password})
    r.raise_for_status()
    data = r.json()
    client.headers["Authorization"] = f"Bearer {data['accessJwt']}"
    return data["accessJwt"]

async def resolve_handle(client, handle):
    r = await client.get("/xrpc/com.atproto.identity.resolveHandle", params={"handle": handle})
    return r.json().get("did") if r.status_code == 200 else None

async def get_record(client, uri):
    parts = uri.split("/")
    if len(parts) < 5: return None
    did, collection, rkey = parts[2], parts[3], parts[4]
    r = await client.get("/xrpc/com.atproto.repo.getRecord", params={"repo": did, "collection": collection, "rkey": rkey})
    return r.json() if r.status_code == 200 else None

async def fetch_thread_chain(client, target_uri, token):
    r = await client.get(
        "https://bsky.social/xrpc/app.bsky.feed.getPostThread",
        params={"uri": target_uri, "depth": 0, "parentHeight": 100},
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
    chain.reverse()
    if not chain: return None
    return {
        "root_uri": chain[0].get("uri"),
        "root_cid": chain[0].get("cid"),
        "parent_cid": chain[-1].get("cid"),
        "chain": chain
    }

async def post_record(client, bot_did, text, reply_obj=None, facets=None):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {"$type": "app.bsky.feed.post", "text": text, "createdAt": created_at}
    if reply_obj: record["reply"] = reply_obj
    if facets: record["facets"] = facets
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": bot_did, "collection": "app.bsky.feed.post", "record": record})
    r.raise_for_status()
    return r.json()

async def post_reply(client, bot_did, text, root_uri, root_cid, parent_uri, parent_cid):
    reply_obj = None
    if parent_cid:
        effective_root = root_uri if root_cid else parent_uri
        effective_root_cid = root_cid if root_cid else parent_cid
        reply_obj = {"root": {"uri": effective_root, "cid": effective_root_cid}, "parent": {"uri": parent_uri, "cid": parent_cid}}
    return await post_record(client, bot_did, text, reply_obj)

async def post_root(client, bot_did, text):
    return await post_record(client, bot_did, text)

async def like_post(client, bot_did, subject_uri, subject_cid):
    created_at = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    record = {"$type": "app.bsky.feed.like", "subject": {"uri": subject_uri, "cid": subject_cid}, "createdAt": created_at}
    r = await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": bot_did, "collection": "app.bsky.feed.like", "record": record})
    r.raise_for_status()
    return r.json()

def get_emoji(status):
    status = (status or "same").lower()
    if status == "new": return "🆕"
    if status == "up": return "↗️"
    if status == "down": return "↘️"
    return "➡️"
