import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import httpx
import base64
import json
from datetime import datetime, timezone
from nacl import encoding, public
from core.config import BOT_DID, PLATFORM_LIMIT, PAT, GITHUB_REPOSITORY
from core.bsky import get_client, login, post_root, like_post, get_emoji
from core.search import chainbase_search
from core.generator import get_model, generate_digest_desc, generate_engagement_plan

def to_monospace(text: str) -> str:
    result = []
    for c in text:
        if 'A' <= c <= 'Z':
            result.append(chr(ord(c) + 0x1D670 - ord('A')))
        elif 'a' <= c <= 'z':
            result.append(chr(ord(c) + 0x1D68A - ord('a')))
        elif '0' <= c <= '9':
            result.append(chr(ord(c) + 0x1D7F6 - ord('0')))
        else:
            result.append(c)
    return ''.join(result)

def encrypt_secret(pk, secret_value):
    pk_obj = public.PublicKey(pk.encode("utf-8"), encoding.Base64Encoder())
    return base64.b64encode(public.SealedBox(pk_obj).encrypt(secret_value.encode("utf-8"))).decode("utf-8")

async def _get_public_key():
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/public-key",
            headers={"Authorization": f"token {PAT}"}
        )
        r.raise_for_status()
        return r.json()

async def _read_secret(secret_name):
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}",
            headers={"Authorization": f"token {PAT}"}
        )
        if r.status_code == 200:
            return r.json().get("value", "").strip()
        return ""

async def _write_secret(secret_name, value):
    async with httpx.AsyncClient() as c:
        key_data = await _get_public_key()
        pk = key_data["key"]
        kid = key_data["key_id"]
        enc = encrypt_secret(pk, value)
        r = await c.put(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}",
            headers={"Authorization": f"token {PAT}"},
            json={"encrypted_value": enc, "key_id": kid}
        )
        r.raise_for_status()

async def _is_due(secret_name, hours):
    raw = await _read_secret(secret_name)
    now = datetime.now(timezone.utc)
    if not raw or raw in ("{}", "null", ""):
        return True, now.isoformat() + "Z"
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if (now - last).total_seconds() >= hours * 3600:
            return True, now.isoformat() + "Z"
        return False, raw
    except:
        return True, now.isoformat() + "Z"

async def post_full_digest(client, llm, trends):
    try:
        if not trends:
            print("[FULL] Skipped: no trends")
            return None
        t = trends[0]
        header = to_monospace("TOP CRYPTO TREND:\n\n")
        title = f"{get_emoji(t.get('rank_status'))} {t['keyword']} 📊 {int(t['score'])}: "
        sig = "\n\n" + to_monospace("Qwen | Chainbase TOPS") + " 💜💛"
        max_desc = PLATFORM_LIMIT - len(header) - len(title) - len(sig)
        if max_desc < 20: max_desc = 20
        raw_llm = generate_digest_desc(llm, t['keyword'], t.get('summary', ''), max_desc)
        desc = raw_llm.strip()
        if not desc:
            print("[FULL] Skipped: empty LLM output")
            return None
        txt = f"{header}{title}{desc}{sig}"
        if len(txt) > PLATFORM_LIMIT:
            safe = PLATFORM_LIMIT - len(sig)
            txt = txt[:safe].rsplit(' ', 1)[0] + sig
        print(f"[FULL] Posting: {txt[:100]}...")
        resp = await post_root(client, BOT_DID, txt)
        uri = resp.get("uri")
        if uri:
            print(f"[FULL] Success: {uri}")
            return uri
        print("[FULL] Failed: no URI in response")
        return None
    except Exception as e:
        print(f"[FULL] Exception: {e}")
        return None

async def post_mini_digest(client, trends):
    try:
        if not trends:
            print("[MINI] Skipped: no trends")
            return None
        header = to_monospace("TOP CRYPTO TRENDS:\n\n")
        sig = "\n\n" + to_monospace("Qwen | Chainbase TOPS") + " 💜💛"
        lines = []
        for t in trends:
            line = f"{get_emoji(t.get('rank_status'))} {t['keyword']} 📊 {int(t['score'])}"
            mono = to_monospace(line)
            if len(header) + len("\n".join(lines + [mono])) + len(sig) <= PLATFORM_LIMIT:
                lines.append(mono)
            else:
                break
        if not lines:
            print("[MINI] Skipped: no lines fit")
            return None
        txt = header + "\n".join(lines) + sig
        print(f"[MINI] Posting: {txt[:100]}...")
        resp = await post_root(client, BOT_DID, txt)
        uri = resp.get("uri")
        if uri:
            print(f"[MINI] Success: {uri}")
            return uri
        print("[MINI] Failed: no URI in response")
        return None
    except Exception as e:
        print(f"[MINI] Exception: {e}")
        return None

async def process_engagement(client, llm, post_uri):
    try:
        r = await client.get("/xrpc/com.atproto.repo.getRecord", params={"repo": post_uri.split("/")[2], "collection": "app.bsky.feed.post", "rkey": post_uri.split("/")[4]})
        if r.status_code != 200: return
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
                        if "replies" in n: crawl(n["replies"])
            crawl(thread.get("replies", []))
        if not comments: return
        plan = generate_engagement_plan(llm, rec_text, comments[:30])
        for uri in plan.get("likes", []):
            c = next((x for x in comments if x["uri"] == uri), None)
            if c: await like_post(client, BOT_DID, uri, c["cid"])
        for r in plan.get("replies", []):
            c = next((x for x in comments if x["uri"] == r["uri"]), None)
            if c:
                txt = r.get("text", "")[:150]
                created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
                record = {"$type": "app.bsky.feed.post", "text": txt, "createdAt": created_at, "reply": {"root": {"uri": post_uri, "cid": c["cid"]}, "parent": {"uri": post_uri, "cid": c["cid"]}}}
                await client.post("/xrpc/com.atproto.repo.createRecord", json={"repo": BOT_DID, "collection": "app.bsky.feed.post", "record": record})
    except Exception as e:
        print(f"[ENGAGEMENT] Error: {e}")

async def main():
    async with get_client() as client:
        await login(client, os.getenv("BOT_HANDLE"), os.getenv("BOT_PASSWORD"))
        llm = get_model()
        trends = await chainbase_search("")
        if not trends:
            print("[MAIN] No trends, exiting")
            return
            
        full_due, full_ts = await _is_due("LAST_FULL_DIGEST", 1)
        mini_due, mini_ts = await _is_due("LAST_MINI_DIGEST", 3)
        print(f"[MAIN] Timers: full_due={full_due} (last={full_ts}), mini_due={mini_due} (last={mini_ts})")
        
        uri = None
        if full_due:
            print("[MAIN] Attempting FULL...")
            uri = await post_full_digest(client, llm, trends)
            if uri:
                await _write_secret("LAST_FULL_DIGEST", full_ts)
                print(f"[MAIN] Saved LAST_FULL_DIGEST={full_ts}")
                
        if not uri and mini_due:
            print("[MAIN] Attempting MINI...")
            uri = await post_mini_digest(client, trends)
            if uri:
                await _write_secret("LAST_MINI_DIGEST", mini_ts)
                print(f"[MAIN] Saved LAST_MINI_DIGEST={mini_ts}")
                
        if uri:
            await asyncio.sleep(15)
            await process_engagement(client, llm, uri)
        else:
            print("[MAIN] Nothing posted")

if __name__ == "__main__":
    asyncio.run(main())
