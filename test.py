import os
import sys
import json
import asyncio
import httpx
import re
import base64
from nacl import encoding, public

import config
import context
import search
import generator
import bsky
import prompts

PAT = os.getenv("PAT")
REPO = os.getenv("GITHUB_REPOSITORY")
POST_URI = os.getenv("POST_URI")
TEST_QUESTION = os.getenv("TEST_QUESTION", "").strip()
USE_TAVILY = os.getenv("USE_TAVILY", "false").lower() == "true"
USE_CHAINBASE = os.getenv("USE_CHAINBASE", "false").lower() == "true"
TEST_RAW_LOGS = os.getenv("TEST_RAW_LOGS", "false").lower() == "true"
BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
BOT_DID = os.getenv("BOT_DID")
OWNER_DID = os.getenv("OWNER_DID")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

async def _extract_link_metadata(url):
    try:
        async with httpx.AsyncClient(follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if r.status_code == 200:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(r.text, 'html.parser')
                t = soup.find('meta', property='og:title')
                d = soup.find('meta', property='og:description')
                return {"title": t.get('content', '') if t else '', "description": d.get('content', '') if d else ''}
    except: pass
    return {"title": "", "description": ""}

async def _fetch_full_thread_context(client, root_uri):
    r = await client.get("/xrpc/com.atproto.feed.getPostThread", params={"uri": root_uri, "depth": 100}, timeout=60)
    if r.status_code != 200:
        return f"[ERROR] getPostThread failed: {r.status_code}"

    data = r.json()
    all_nodes = []
    quoted_cache = {}

    async def collect_nodes(node, parent_uri=None):
        if not node: return
        if node.get("$type") in ["app.bsky.feed.defs#notFoundPost", "app.bsky.feed.defs#blockedPost"]: return

        post = node.get("post", {})
        record = post.get("record", {})
        if not record: return

        node_uri = post.get("uri")
        author = post.get("author", {})
        did = author.get("did", "")
        handle = author.get("handle", did.split(":")[-1] if ":" in did else "unknown")
        txt = record.get("text", "")
        embed = record.get("embed")
        alts = []

        if embed and isinstance(embed, dict):
            etype = embed.get("$type", "")
            if etype == "app.bsky.embed.record":
                rec_ref = embed.get("record", {})
                if rec_ref and rec_ref.get("uri"):
                    if rec_ref["uri"] not in quoted_cache:
                        parts = rec_ref["uri"].split("/")
                        if len(parts) >= 5:
                            q = await client.get("/xrpc/com.atproto.repo.getRecord",
                                                 params={"repo": parts[2], "collection": parts[3], "rkey": parts[4]})
                            if q.status_code == 200:
                                quoted_cache[rec_ref["uri"]] = q.json().get("value", {}).get("text", "")[:200]
                    if rec_ref["uri"] in quoted_cache:
                        q_author = rec_ref["uri"].split("/")[2]
                        txt = f"{txt}\n[🔁 @{q_author}: {quoted_cache[rec_ref['uri']]}]"
            elif etype == "app.bsky.embed.recordWithMedia":
                rec_ref = embed.get("record", {})
                media = embed.get("media", {})
                if rec_ref and rec_ref.get("uri"):
                    if rec_ref["uri"] not in quoted_cache:
                        parts = rec_ref["uri"].split("/")
                        if len(parts) >= 5:
                            q = await client.get("/xrpc/com.atproto.repo.getRecord",
                                                 params={"repo": parts[2], "collection": parts[3], "rkey": parts[4]})
                            if q.status_code == 200:
                                quoted_cache[rec_ref["uri"]] = q.json().get("value", {}).get("text", "")[:200]
                    if rec_ref["uri"] in quoted_cache:
                        q_author = rec_ref["uri"].split("/")[2]
                        txt = f"{txt}\n[🔁 @{q_author}: {quoted_cache[rec_ref['uri']]}]"
                if media and media.get("$type") == "app.bsky.embed.images":
                    for img in media.get("images", []):
                        if isinstance(img, dict) and img.get("alt"):
                            alts.append(f"@{handle} image: {img['alt']}")
            elif etype == "app.bsky.embed.images":
                for img in embed.get("images", []):
                    if isinstance(img, dict) and img.get("alt"):
                        alts.append(f"@{handle} image: {img['alt']}")
            elif etype == "app.bsky.embed.external":
                ext = embed.get("external", {})
                if isinstance(ext, dict) and ext.get("alt"):
                    alts.append(f"Link: {ext['alt']}")

        all_nodes.append({"handle": handle, "text": txt, "alts": alts, "is_root": (parent_uri is None)})

        for reply_node in node.get("replies", []):
            if isinstance(reply_node, dict):
                await collect_nodes(reply_node, node_uri)

    await collect_nodes(data.get("thread", {}))

    lines = []
    all_alts = []
    for node in all_nodes:
        marker = " [ROOT]" if node["is_root"] else ""
        lines.append(f"@{node['handle']}{marker}: {node['text']}")
        all_alts.extend(node["alts"])

    if all_nodes and "http" in all_nodes[0]["text"]:
        urls = re.findall(r'https?://[^\s]+', all_nodes[0]["text"])
        if urls:
            lm = await _extract_link_metadata(urls[0])
            if lm.get("title"):
                lines[0] += f" [Linked: {lm['title']}]"

    if all_alts:
        lines.append(f"\n[HINT from image alt: {'; '.join(list(set(all_alts)))}]")

    return "\n".join(lines)

def _get_public_key():
    url = f"https://api.github.com/repos/{REPO}/actions/secrets/public-key"
    headers = {"Authorization": f"token {PAT}", "Accept": "application/vnd.github.v3+json"}
    r = httpx.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()

def _encrypt_secret(public_key_str, value):
    public_key = public.PublicKey(public_key_str.encode("utf-8"), encoding.Base64Encoder())
    sealed_box = public.SealedBox(public_key)
    encrypted = sealed_box.encrypt(value.encode("utf-8"))
    return base64.b64encode(encrypted).decode("utf-8")

def _write_test_secret(name, value):
    key_data = _get_public_key()
    encrypted_value = _encrypt_secret(key_data["key"], value)
    url = f"https://api.github.com/repos/{REPO}/actions/secrets/{name}"
    headers = {"Authorization": f"token {PAT}", "Accept": "application/vnd.github.v3+json"}
    payload = {"encrypted_value": encrypted_value, "key_id": key_data["key_id"]}
    r = httpx.put(url, headers=headers, json=payload, timeout=15)
    r.raise_for_status()

async def main():
    if not POST_URI:
        print("[TEST] ERROR: POST_URI not provided.")
        sys.exit(1)

    print("=" * 60)
    print("[TEST] STARTING ISOLATED RUN")
    print(f"[TEST] URI: {POST_URI}")
    print(f"[TEST] Question: '{TEST_QUESTION}'" if TEST_QUESTION else "[TEST] Question: (none)")
    print(f"[TEST] Sources: tavily={USE_TAVILY}, chainbase={USE_CHAINBASE}")
    print(f"[TEST] Raw logs: {TEST_RAW_LOGS}")
    print("=" * 60)

    async with bsky.get_client() as client:
        try:
            await bsky.login(client, BOT_HANDLE, BOT_PASSWORD)
            print("[TEST] Authenticated successfully.")
        except Exception as e:
            print(f"[TEST] Auth failed: {e}")
            sys.exit(1)

        normalized_uri = await bsky.normalize_uri(client, POST_URI)
        if not normalized_uri:
            print(f"[TEST] ERROR: Could not parse URI: {POST_URI}")
            sys.exit(1)
        
        print(f"[TEST] Normalized URI: {normalized_uri}")

        rec = await bsky.get_record(client, normalized_uri)
        if not rec:
            print(f"[TEST] ERROR: Record not found for URI: {normalized_uri}")
            sys.exit(1)

        user_text = rec["value"].get("text", "")
        reply_info = rec["value"].get("reply", {})
        root_uri = reply_info.get("root", {}).get("uri", normalized_uri)
        root_cid = reply_info.get("root", {}).get("cid", "")
        parent_cid = rec.get("cid", "")
        thread_id = root_uri

        print(f"[TEST] User text: {user_text[:200]}...")
        print(f"[TEST] Root URI: {root_uri}")

        print("\n" + "=" * 60)
        print("[STEP 1] FETCHING FULL THREAD CONTEXT (OLD BOT LOGIC)")
        print("=" * 60)
        
        thread_context = await _fetch_full_thread_context(client, root_uri)
        print(f"[STEP 1] Length: {len(thread_context)} chars")
        print(f"[STEP 1] Content:\n{thread_context if thread_context else '(Empty)'}\n")

        print("\n" + "=" * 60)
        print("[STEP 2] LOADING PERSISTED CONTEXT FROM SECRETS")
        print("=" * 60)

        persisted_context = ""
        secret_name = "TEST_CONTEXT_0"
        raw_secret = os.getenv(secret_name, "")
        
        if raw_secret:
            try:
                data = json.loads(raw_secret)
                if data.get("thread_id") == thread_id:
                    persisted_context = data.get("content", "")
                    print(f"[STEP 2] Found matching secret '{secret_name}'")
                    print(f"[STEP 2] Secret Content:\n{persisted_context}\n")
                else:
                    print(f"[STEP 2] Secret '{secret_name}' exists but thread_id mismatch")
            except Exception as e:
                print(f"[STEP 2] Error parsing secret: {e}")
        else:
            print(f"[STEP 2] No secret '{secret_name}' found or empty.")
        
        print(f"[STEP 2] Final Persisted Context Length: {len(persisted_context)}")

        search_results = ""
        search_valid = False
        search_type = None

        has_question = bool(TEST_QUESTION)
        has_source = USE_TAVILY or USE_CHAINBASE

        if has_question and has_source:
            print("\n" + "=" * 60)
            print("[STEP 3] FETCHING SEARCH RESULTS")
            print("=" * 60)

            if USE_TAVILY:
                search_type = "tavily"
            elif USE_CHAINBASE:
                search_type = "chainbase"
            
            print(f"[STEP 3] Provider: {search_type}")
            print(f"[STEP 3] Query: '{TEST_QUESTION}'")
            
            search_params = generator.extract_search_params(generator.get_model(), TEST_QUESTION)
            print(f"[STEP 3] Extracted Params: {search_params}")
            
            provider = search.SEARCH_PROVIDERS.get(search_type)
            if provider:
                func = provider["func"]
                supported = provider.get("supports", [])
                kwargs = {k: v for k, v in search_params.items() if k in supported}
                kwargs.pop('query', None)
                
                search_results = await func(search_params["query"], **kwargs)
                search_valid = search.is_search_result_valid(search_results, search_type)
                
                print(f"[STEP 3] Valid: {search_valid}")
                print(f"[STEP 3] Results Length: {len(search_results)}")
                print(f"[STEP 3] Content:\n{search_results[:500]}{'...' if len(search_results)>500 else ''}\n")
        elif has_question and not has_source:
            print("\n[STEP 3] SKIPPED: Question provided but no source selected.")
        else:
            print("\n[STEP 3] SKIPPED: No question or sources enabled.")

        print("\n" + "=" * 60)
        print("[STEP 4] ASSEMBLING FINAL CONTEXT & GENERATING REPLY")
        print("=" * 60)

        full_context = ""
        if persisted_context:
            full_context += f"Thread Summary (Memory):\n{persisted_context}\n\n"
        if thread_context:
            full_context += f"Thread Context (Live):\n{thread_context}\n\n"
        if search_results and search_valid:
            full_context += f"Search Results:\n{search_results}\n\n"

        if has_question and has_source:
            debug_prompt = (
                f"  system\n{prompts.ANSWER_SYSTEM}\n"
                f"  user\n{full_context}User Question:\n{TEST_QUESTION}\n"
                f"  assistant\n"
            )
            
            print(f"[STEP 4] Final Prompt to Model:")
            print("-" * 40)
            print(debug_prompt)
            print("-" * 40)
            
            llm = generator.get_model()
            reply = generator.get_answer(
                llm,
                memory_context=persisted_context,
                fresh_context=thread_context,
                search_results=search_results if search_valid else "",
                user_text=TEST_QUESTION,
                do_search=True,
                search_type=search_type
            )
            
            print(f"\n[STEP 4] Generated Reply:")
            print("-" * 40)
            print(reply)
            print("-" * 40)

            print("\n[STEP 4] Simulating memory update...")
            new_summary = generator.update_summary(llm, persisted_context, TEST_QUESTION, reply)
            payload = json.dumps({"thread_id": thread_id, "content": new_summary, "ts": 0}, ensure_ascii=False)
            _write_test_secret("TEST_CONTEXT_0", payload)
            print("[STEP 4] Secret 'TEST_CONTEXT_0' updated with new summary.")
        else:
            print("[STEP 4] SKIPPED: No question or no source — reply generation disabled.")
            print("[STEP 4] Context assembly complete. Check logs above.")

        print("\n" + "=" * 60)
        print("[TEST] FINISHED")
        print("[TEST] Post simulated. Nothing sent to Bluesky.")
        print("[TEST] LAST_PROCESSED not modified.")
        print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())
