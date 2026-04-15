import os
import sys
import json
import asyncio
import httpx
import base64
from nacl import encoding, public

import config
import memory
import search
import generator
import bsky
import prompts

PAT = os.getenv("PAT")
REPO = os.getenv("GITHUB_REPOSITORY")
POST_URI = os.getenv("POST_URI")
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

async def main():
    if not POST_URI:
        print("[TEST] ERROR: POST_URI not provided.")
        sys.exit(1)

    print(f"[TEST] Starting isolated run for: {POST_URI}")
    print("[TEST] Mode: DRY-RUN (No posts, No LAST_PROCESSED update)")

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

        print(f"[TEST] User text: {user_text}")
        print(f"[TEST] Root URI: {root_uri}")

        fresh_context = await bsky.get_context_string(client, root_uri, BOT_HANDLE, owner_did=OWNER_DID)
        print(f"[TEST] Fresh Context Length: {len(fresh_context)}")
        print(f"[TEST] Fresh Context RAW:\n{fresh_context}\n")

        persisted_context = ""
        try:
            raw = os.getenv("TEST_CONTEXT_0", "")
            if raw:
                data = json.loads(raw)
                if data.get("thread_id") == thread_id:
                    persisted_context = data.get("content", "")
        except:
            pass
        print(f"[TEST] Persisted Context Length: {len(persisted_context)}")
        if persisted_context:
            print(f"[TEST] Persisted Context RAW:\n{persisted_context[:300]}...\n")

        search_results = ""
        search_valid = False
        search_type = "tavily"
        if "!t" in user_text.lower():
            search_type = "tavily"
        elif "!c" in user_text.lower():
            search_type = "chainbase"
        
        if search_type in ["tavily", "chainbase"]:
            print(f"[TEST] Running search ({search_type})...")
            search_params = generator.extract_search_params(generator.get_model(), user_text)
            print(f"[TEST] Search Params: {search_params}")
            
            provider = search.SEARCH_PROVIDERS.get(search_type)
            if provider:
                func = provider["func"]
                supported = provider.get("supports", [])
                kwargs = {k: v for k, v in search_params.items() if k in supported}
                kwargs.pop('query', None)
                search_results = await func(search_params["query"], **kwargs)
                search_valid = search.is_search_result_valid(search_results, search_type)
                print(f"[TEST] Search Valid: {search_valid}")
                print(f"[TEST] Search Results RAW:\n{search_results[:500]}...\n")

        full_context = ""
        if persisted_context:
            full_context += f"Thread Summary:\n{persisted_context}\n\n"
        if fresh_context:
            full_context += f"Recent Context:\n{fresh_context}\n\n"
        if search_results and search_valid:
            full_context += f"Search Results:\n{search_results}\n\n"

        debug_prompt = (
            f"  system\n{prompts.ANSWER_SYSTEM}\n"
            f"  user\n{full_context}User Question:\n{user_text}\n"
            f"  assistant\n"
        )
        print(f"[TEST] FULL PROMPT TO MODEL:\n{debug_prompt}\n")

        llm = generator.get_model()
        reply = generator.get_answer(
            llm,
            memory_context=persisted_context,
            fresh_context=fresh_context,
            search_results=search_results if search_valid else "",
            user_text=user_text,
            do_search=bool(search_results),
            search_type=search_type
        )
        print(f"[TEST] Generated Reply:\n{reply}\n")

        print("[TEST] Simulating memory update (TEST_CONTEXT_0)...")
        new_summary = generator.update_summary(llm, persisted_context, user_text, reply)
        payload = json.dumps({"thread_id": thread_id, "content": new_summary, "ts": 0}, ensure_ascii=False)
        _write_test_secret("TEST_CONTEXT_0", payload)
        print("[TEST] TEST_CONTEXT_0 secret created/updated.")

        print("[TEST] Post simulated. Nothing sent to Bluesky.")
        print("[TEST] LAST_PROCESSED not modified.")
        print("[TEST] SUCCESS. Check logs above for context pipeline verification.")

if __name__ == "__main__":
    asyncio.run(main())
