import os
import sys
import json
import asyncio
import httpx
import re
import base64
from nacl import encoding, public
from bs4 import BeautifulSoup

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

async def _extract_full_post_context(rec):
    if not rec or "value" not in rec:
        return ""
    post = rec.get("value", {})
    author = rec.get("author", {})
    txt = post.get("text", "")
    embed = post.get("embed", {})
    embed_parts = []
    alts = []

    if embed:
        etype = embed.get("$type", "")
        if etype == "app.bsky.embed.external":
            ext = embed.get("external", {})
            title = ext.get("title", "").strip()
            desc = ext.get("description", "").strip()
            uri = ext.get("uri", "").strip()
            if title:
                embed_parts.append(f"[Link: {title}]")
            if desc:
                embed_parts.append(f"[Desc: {desc[:150]}]")
            if uri and not uri.startswith("https://bsky.app"):
                embed_parts.append(f"[URL: {uri}]")
        elif etype == "app.bsky.embed.images":
            for i, img in enumerate(embed.get("images", []), 1):
                alt = img.get("alt", "").strip()
                if alt:
                    alts.append(f"Image {i}: {alt}")
                    embed_parts.append(f"[Image {i}: {alt}]")
                else:
                    embed_parts.append(f"[Image {i}]")
        elif etype == "app.bsky.embed.record":
            rec_ref = embed.get("record", {})
            if rec_ref.get("$type") == "app.bsky.feed.post":
                val = rec_ref.get("value", {})
                quote = val.get("text", "")[:150]
                q_author = rec_ref.get("author", {}).get("handle", "")
                if quote:
                    embed_parts.append(f"[Quote @{q_author}: {quote}]")

    if "http" in txt:
        urls = re.findall(r'https?://[^\s]+', txt)
        if urls:
            async with httpx.AsyncClient(follow_redirects=True) as c:
                r = await c.get(urls[0], headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                if r.status_code == 200:
                    soup = BeautifulSoup(r.text, 'html.parser')
                    og_title = soup.find('meta', property='og:title')
                    og_desc = soup.find('meta', property='og:description')
                    t = og_title.get('content', '').strip() if og_title else ''
                    d = og_desc.get('content', '').strip() if og_desc else ''
                    if t:
                        txt = f"{txt}\n[Linked: {t}]"

    if alts:
        txt = f"{txt}\n[HINT from image alt: {'; '.join(alts)}]"
    if embed_parts:
        txt = f"{txt} {' '.join(embed_parts)}"

    handle = author.get("handle", "")
    return f"@{handle} [ROOT]: {txt}"

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
        print("[STEP 1] FETCHING THREAD CONTEXT FROM BLUESKY")
        print("=" * 60)
        
        root_rec = await bsky.get_record(client, root_uri)
        thread_context = await _extract_full_post_context(root_rec)
        
        print(f"[STEP 1] Length: {len(thread_context)} chars")
        print(f"[STEP 1] Content:\n{thread_context if thread_context else '(Empty)'}\n")

        if TEST_RAW_LOGS and root_rec:
            print(f"[TEST_RAW] Root Record JSON:\n{json.dumps(root_rec, indent=2, ensure_ascii=False)[:1500]}...\n")

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
                
                if TEST_RAW_LOGS and search_type == "tavily":
                    print(f"[TEST_RAW] Tavily Payload:\n{json.dumps({'query': search_params['query'], 'search_depth': 'basic', 'include_answer': True, 'include_raw_content': True, 'max_results': 5, **kwargs}, indent=2)}\n")
                
                search_results = await func(search_params["query"], **kwargs)
                search_valid = search.is_search_result_valid(search_results, search_type)
                
                print(f"[STEP 3] Valid: {search_valid}")
                print(f"[STEP 3] Results Length: {len(search_results)}")
                print(f"[STEP 3] Content:\n{search_results[:500]}{'...' if len(search_results)>500 else ''}\n")
                
                if TEST_RAW_LOGS:
                    print(f"[TEST_RAW] Full Search Results:\n{search_results}\n")
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
            
            if TEST_RAW_LOGS:
                print(f"[TEST_RAW] Prompt Tokens (estimated): {len(debug_prompt) // 4}")

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
            
            if TEST_RAW_LOGS:
                print(f"[TEST_RAW] Reply Tokens (estimated): {len(reply) // 4}")

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
