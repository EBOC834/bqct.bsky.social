import re
from core.config import PLATFORM_LIMIT

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

def calc_body_limit(header, signature, buffer=10):
    return max(10, PLATFORM_LIMIT - len(header) - len(signature) - buffer)

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
