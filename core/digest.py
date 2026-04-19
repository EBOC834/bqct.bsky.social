# core/digest.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import base64
import httpx
import re
from datetime import datetime, timezone
from nacl import encoding, public
from core.config import BOT_DID, PLATFORM_LIMIT, PAT, GITHUB_REPOSITORY, load_prompts
from core.bsky import get_client, login, post_root, like_post, get_emoji
from core.search import chainbase_search
from core.generator import get_model, generate_engagement_plan

PROMPTS = load_prompts()

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

def _encrypt_secret(pk: str, secret_value: str) -> str:
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

async def _read_secret(secret_name: str) -> str:
    async with httpx.AsyncClient() as c:
        r = await c.get(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}",
            headers={"Authorization": f"token {PAT}"}
        )
        if r.status_code == 200:
            return r.json().get("value", "").strip()
        return ""

async def _write_secret(secret_name: str, value: str):
    async with httpx.AsyncClient() as c:
        key_data = await _get_public_key()
        pk = key_data["key"]
        kid = key_data["key_id"]
        enc = _encrypt_secret(pk, value)
        r = await c.put(
            f"https://api.github.com/repos/{GITHUB_REPOSITORY}/actions/secrets/{secret_name}",
            headers={"Authorization": f"token {PAT}"},
            json={"encrypted_value": enc, "key_id": kid}
        )
        r.raise_for_status()

def _is_due(raw: str, hours: int):
    now = datetime.now(timezone.utc)
    if not raw or raw in ("{}", "null", ""):
        return True, now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if (now - last).total_seconds() >= hours * 3600:
            return True, now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        return False, raw
    except:
        return True, now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

def _extract_text(response):
    if isinstance(response, str): return response.strip()
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict): return choices[0].get("text", "").strip()
    return ""

def generate_digest_desc(llm, keyword, summary, max_chars):
    prompt = (
        f"Write a UNIQUE, fact-based crypto update for '{keyword}'.\n"
        f"STRICT RULES:\n"
        f"1. NEVER copy phrases from the context. Rephrase entirely.\n"
        f"2. Focus on catalyst, price action, or key metric.\n"
        f"3. Max {max_chars} chars. Plain text only.\n"
        f"4. Output ONLY the description.\n"
        f"Context: {summary}\n"
        f"Description:"
    )
    response = llm(prompt, max_tokens=min(max_chars + 30, 120), temperature=0.7)
    raw = _extract_text(response).strip()
    raw = re.sub(r'```[^`]*```', '', raw)
    raw = re.sub(r'[*_~`]', '', raw)
    if raw.lower().startswith(keyword.lower()):
        raw = raw[len(keyword):].lstrip(":., ")
    if len(raw) > max_chars:
        raw = raw[:max_chars].rsplit(' ', 1)[0]
    last_punct = max(raw.rfind('.'), raw.rfind('!'), raw.rfind('?'))
    if last_punct > max_chars * 0.6:
        raw = raw[:last_punct + 1]
    return raw.strip()

async def post_full_digest(client, llm, trends):
    try:
        if not trends:
            return None
        t = trends[0]
        keyword = t['keyword']
        score = int(t['score'])
        rank_status = t.get('rank_status', 'same')
        summary = t.get('summary', '')
        
        emoji_char = get_emoji(rank_status)
        header = to_monospace("TOP CRYPTO TREND:\n\n")
        title = f"{emoji_char} {keyword} 📊 {score}: "
        sig = "\n\n" + to_monospace("Qwen | Chainbase TOPS") + " 💜💛"
        max_desc = PLATFORM_LIMIT - len(header) - len(title) - len(sig)
        
        if max_desc < 20:
            return None
            
        raw_llm = generate_digest_desc(llm, keyword, summary, max_desc)
        desc = raw_llm.strip()
        if not desc:
            return None
            
        txt = f"{header}{title}{desc}{sig}"
        if len(txt) > PLATFORM_LIMIT:
            safe = PLATFORM_LIMIT - len(sig)
            txt = txt[:safe].rsplit(' ', 1)[0] + sig
            
        resp = await post_root(client, BOT_DID, txt)
        uri = resp.get("uri")
        if uri:
            return uri
        return None
    except Exception as e:
        return None

async def post_mini_digest(client, trends):
    try:
        if not trends:
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
            return None
        txt = header + "\n".join(lines) + sig
        resp = await post_root(client, BOT_DID, txt)
        uri = resp.get("uri")
        if uri:
            return uri
        return None
    except Exception as e:
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
        pass

async def main():
    async with get_client() as client:
        await login(client, os.getenv("BOT_HANDLE"), os.getenv("BOT_PASSWORD"))
        llm = get_model()
        trends = await chainbase_search("")
        if not trends:
            return
            
        full_raw = await _read_secret("LAST_FULL_DIGEST")
        mini_raw = await _read_secret("LAST_MINI_DIGEST")
        full_due, full_ts = _is_due(full_raw, 1)
        mini_due, mini_ts = _is_due(mini_raw, 3)
        
        uri = None
        if full_due:
            uri = await post_full_digest(client, llm, trends)
            if uri:
                await _write_secret("LAST_FULL_DIGEST", full_ts)
        if not uri and mini_due:
            uri = await post_mini_digest(client, trends)
            if uri:
                await _write_secret("LAST_MINI_DIGEST", mini_ts)
        if uri:
            await asyncio.sleep(15)
            await process_engagement(client, llm, uri)

if __name__ == "__main__":
    asyncio.run(main())
