import os
import yaml

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROMPTS_DIR = os.path.join(BASE_DIR, "prompts")
STATE_FILE = os.path.join(BASE_DIR, "state", "runtime.json")

MODEL_PATH = os.getenv("MODEL_PATH", "models/qwen2.5-coder-14b-instruct-q5_k_m.gguf")
MODEL_N_CTX = int(os.getenv("MODEL_N_CTX", "8192"))
MODEL_N_THREADS = int(os.getenv("MODEL_N_THREADS", "2"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "512"))
PLATFORM_LIMIT = int(os.getenv("RESPONSE_MAX_CHARS", "300"))
CONTEXT_SLOT_COUNT = int(os.getenv("CONTEXT_SLOT_COUNT", "10"))
SEARCH_TIMEOUT = int(os.getenv("SEARCH_TIMEOUT", "30"))
BOT_DID = os.getenv("BOT_DID", "")
BOT_HANDLE = os.getenv("BOT_HANDLE", "")
BOT_PASSWORD = os.getenv("BOT_PASSWORD", "")
OWNER_DID = os.getenv("OWNER_DID", "")
PAT = os.getenv("PAT", "")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

def load_prompts():
    prompts = {}
    for fname in os.listdir(PROMPTS_DIR):
        if fname.endswith(".yaml"):
            with open(os.path.join(PROMPTS_DIR, fname), "r", encoding="utf-8") as f:
                prompts.update(yaml.safe_load(f))
    return prompts
