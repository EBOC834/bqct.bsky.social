import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import os
import asyncio
from datetime import datetime, timezone
from core.config import BOT_DID, PLATFORM_LIMIT
from core.bsky import get_client, login, post_root, like_post, get_emoji
from core.search import chainbase_search
from core.generator import get_model, generate_digest_desc, generate_engagement_plan
from core.state import load_timer, save_timer

async def post_full_digest(client, llm, trends):
    if not trends:
        return None
    t = trends[0]
    header = "TOP CRYPTO TREND:\n"
    title = f"{get_emoji(t.get('rank_status'))} {t['keyword']} 📊 {int(t['score'])}: "
    sig = "\nQwen | Chainbase TOPS 💜💛"
    max_desc = PLATFORM_LIMIT - len(header) - len(sig) - len(title)
    desc = generate_digest_desc(llm, t['keyword'], t.get('summary', ''), max(20, max_desc))
    txt = header + title + desc + sig
    if len(txt) > PLATFORM_LIMIT:
        txt = txt[:PLATFORM_LIMIT].rsplit(' ', 1)[0]
    resp = await post_root(client, BOT_DID, txt)
    return resp.get("uri")

async def post_mini_digest(client, trends):
    if not trends:
        return None
    header = "TOP CRYPTO TRENDS:\n"
    sig = "\nQwen | Chainbase TOPS 💜💛"
    lines = []
    for t in trends:
        line = f"{get_emoji(t.get('rank_status'))} {t['keyword']} 📊 {int(t['score'])}"
        if len(header) + len("\n".join(lines + [line])) + len(sig) <= PLATFORM_LIMIT:
            lines.append(line)
        else:
            break
    if not lines:
        return None
    txt = header + "\n".join(lines) + sig
    resp = await post_root(client, BOT_DID, txt)
    return resp.get("uri")

async def process_engagement(client, llm, post_uri):
    r = await client.get("/xrpc/com.atproto.repo.getRecord", params={"repo": post_uri.split("/")[2], "collection": "app.bsky.feed.post", "rkey": post_uri.split("/")[4]})
    if r.status_code != 200:
        return
    rec_text = r.json().get("value", {}).get("text", "")
    comments = []
    token = client.headers.get("Authorization", "").replace("Bearer ", "")
    r = await client.get("https://bsky.social/xrpc/app.bsky.feed.getPostThread", params={"uri": post_uri, "depth": 50, "parentHeight": 0}, headers={"Authorization": f"Bearer {token}"})
    if r.status_code == 200:
        thread = r.json().get("thread", {})
        def crawl(nodes):
            for n in nodes:
                if isinstance(n, dict) and "post" in n:
                    p = n["post"]
                    if p.get("author", {}).get("did") != BOT_DID:
                        comments.append({"uri": p["uri"], "cid": p["cid"], "handle": p["author"].get("handle", ""), "text": p.get("record", {}).get("text", "")})
                    if "replies" in n:
                        crawl(n["replies"])
        crawl(thread.get("replies", []))
    if not comments:
        return
    plan = generate_engagement_plan(llm, rec_text, comments[:30])
    for uri in plan.get("likes", []):
        c = next((x for x in comments if x["uri"] == uri), None)
        if c:
            await like_post(client, BOT_DID, uri, c["cid"])
    for r in plan.get("replies", []):
        c = next((x for x in comments if x["uri"] == r["uri"]), None)
        if c:
            txt = r.get("text", "")[:150]
            created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            record = {"$type": "app.bsky.feed.post", "text": txt, "createdAt": created_at, "reply": {"root": {"uri": post_uri, "cid": c["cid"]}, "parent": {"uri": post_uri, "cid": c["cid"]}}}
            await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": BOT_DID, "collection": "app.bsky.feed.post", "record": record})

async def main():
    now = datetime.now(timezone.utc).isoformat()
    async with get_client() as client:
        await login(client, os.getenv("BOT_HANDLE"), os.getenv("BOT_PASSWORD"))
        llm = get_model()
        trends = await chainbase_search("")
        if not trends:
            return
        full_due = not load_timer("LAST_FULL_DIGEST")
        mini_due = not load_timer("LAST_MINI_DIGEST")
        uri = None
        if full_due:
            uri = await post_full_digest(client, llm, trends)
            if uri:
                save_timer("LAST_FULL_DIGEST", now)
        if mini_due and not uri:
            uri = await post_mini_digest(client, trends)
            if uri:
                save_timer("LAST_MINI_DIGEST", now)
        if uri:
            await asyncio.sleep(15)
            await process_engagement(client, llm, uri)

if __name__ == "__main__":
    asyncio.run(main())
