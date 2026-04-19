# core/utils.py
import re
import logging
import os
from functools import wraps
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

def retry_http():
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, ConnectionError)),
        reraise=True
    )

def extract_text(response):
    if isinstance(response, str):
        return response.strip()
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices and isinstance(choices[0], dict):
            return choices[0].get("text", "").strip()
    return ""

def clean_artifacts(text):
    if not text:
        return ""
    text = re.sub(r'\s*\[score:\s*\d+\]\s*:', ':', text)
    text = re.sub(r'\s*\[\d+\s*characters?\]', '', text)
    text = re.sub(r'\s*[!|/][tc]\b', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    text = re.sub(r'^\[ROOT\]\s*@[^\s]+:\s*', '', text)
    text = re.sub(r'^\[[A-Z_]+\]\s*', '', text)
    return text.strip()

def extract_urls(text):
    return re.findall(r'https?://[^\s<>"{}|\\^`\[\]]+', text)

def normalize_uri(uri):
    if not uri:
        return ""
    if uri.startswith("at://"):
        return uri
    match = re.match(r"https://bsky\.app/profile/([^/]+)/post/([^/?#]+)", uri)
    if match:
        handle, rkey = match.groups()
        if handle.startswith("did:plc:"):
            return f"at://{handle}/app.bsky.feed.post/{rkey}"
    return uri

def calc_body_limit(header, signature, buffer=10, platform_limit=300):
    return max(10, platform_limit - len(header) - len(signature) - buffer)
