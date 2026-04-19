# core/digest.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
from datetime import datetime, timezone
from core.config import BOT_DID, PLATFORM_LIMIT
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

def _read_state():
    state_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "runtime.json")
    if os.path.exists(state_file):
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"timers": {}}

def _write_state(data):
    state_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "runtime.json")
    os.makedirs(os.path.dirname(state_file), exist_ok=True)
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _is_due(secret_name, hours):
    state = _read_state()
    timers = state.get("timers", {})
    raw = timers.get(secret_name, "")
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

def _save_timer(secret_name, value):
    state = _read_state()
    if "timers" not in state: state["timers"] = {}
    state["timers"][secret_name] = value
    _write_state(state)

async def post_full_digest(client, llm, trends):
    try:
        if not trends:
            print("[FULL] ERROR: No trends data")
            return None
        t = trends[0]
        keyword = t['keyword']
        score = int(t['score'])
        rank_status = t.get('rank_status', 'same')
        summary = t.get('summary', '')
        
        print(f"[FULL] Input data:")
        print(f"  Keyword: {keyword}")
        print(f"  Score: {score}")
        print(f"  Rank: {rank_status}")
        print(f"  Summary ({len(summary)} chars): {summary[:200]}...")
        
        emoji_char = get_emoji(rank_status)
        header = to_monospace("TOP CRYPTO TREND:\n\n")
        title = f"{emoji_char} {keyword} 📊 {score}: "
        sig = "\n\n" + to_monospace("Qwen | Chainbase TOPS") + " 💜💛"
        
        max_desc = PLATFORM_LIMIT - len(header) - len(title) - len(sig)
        print(f"[FULL] Character limits:")
        print(f"  Platform limit: {PLATFORM_LIMIT}")
        print(f"  Header len: {len(header)}")
        print(f"  Title len: {len(title)}")
        print(f"  Signature len: {len(sig)}")
        print(f"  Max description: {max_desc} chars")
        
        if max_desc < 20:
            print(f"[FULL] ERROR: max_desc too small ({max_desc})")
            return None
        
        print(f"[FULL] Calling LLM with max_desc={max_desc}...")
        raw_llm = generate_digest_desc(llm, keyword, summary, max_desc)
        print(f"[FULL] LLM raw output ({len(raw_llm)} chars): '{raw_llm}'")
        
        desc = raw_llm.strip()
        if not desc:
            print("[FULL] ERROR: LLM returned empty description")
            return None
        
        print(f"[FULL] Generated description ({len(desc)} chars): {desc}")
        
        txt = f"{header}{title}{desc}{sig}"
        print(f"[FULL] Full post BEFORE trim ({len(txt)} chars):")
        print(txt)
        
        if len(txt) > PLATFORM_LIMIT:
            safe = PLATFORM_LIMIT - len(sig)
            trimmed = txt[:safe].rsplit(' ', 1)[0]
            txt = trimmed + sig
            print(f"[FULL] Post trimmed to {len(txt)} chars")
        
        print(f"[FULL] FINAL POST ({len(txt)} chars):")
        print(txt)
        
        resp = await post_root(client, BOT_DID, txt)
        uri = resp.get("uri")
        if uri:
            print(f"[FULL] SUCCESS: Posted {uri}")
            return uri
        print("[FULL] ERROR: post_root returned no URI")
        return None
    except Exception as e:
        print(f"[FULL] EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return None

async def post_mini_digest(client, trends):
    try:
        if not trends:
            print("[MINI] ERROR: No trends data")
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
            print("[MINI] ERROR: No lines fit")
            return None
        txt = header + "\n".join(lines) + sig
        print(f"[MINI] Posted ({len(txt)} chars): {txt[:100]}...")
        resp = await post_root(client, BOT_DID, txt)
        uri = resp.get("uri")
        if uri:
            print(f"[MINI] SUCCESS: {uri}")
            return uri
        print("[MINI] ERROR: No URI")
        return None
    except Exception as e:
        print(f"[MINI] EXCEPTION: {e}")
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
    now = datetime.now(timezone.utc).isoformat()
    async with get_client() as client:
        await login(client, os.getenv("BOT_HANDLE"), os.getenv("BOT_PASSWORD"))
        llm = get_model()
        trends = await chainbase_search("")
        if not trends:
            print("[MAIN] ERROR: No trends from Chainbase")
            return
        print(f"[MAIN] Got {len(trends)} trends from Chainbase")
        full_due, full_ts = _is_due("LAST_FULL_DIGEST", 1)
        mini_due, mini_ts = _is_due("LAST_MINI_DIGEST", 3)
        print(f"[MAIN] Timers: full_due={full_due} (last={full_ts}), mini_due={mini_due} (last={mini_ts})")
        uri = None
        if full_due:
            print("[MAIN] Attempting FULL...")
            uri = await post_full_digest(client, llm, trends)
            if uri:
                _save_timer("LAST_FULL_DIGEST", full_ts)
                print(f"[MAIN] Saved LAST_FULL_DIGEST={full_ts}")
        if not uri and mini_due:
            print("[MAIN] Attempting MINI...")
            uri = await post_mini_digest(client, trends)
            if uri:
                _save_timer("LAST_MINI_DIGEST", mini_ts)
                print(f"[MAIN] Saved LAST_MINI_DIGEST={mini_ts}")
        if uri:
            await asyncio.sleep(15)
            await process_engagement(client, llm, uri)
        else:
            print("[MAIN] Nothing posted")

if __name__ == "__main__":
    asyncio.run(main())
