# core/config.py
import os
import yaml

MODEL_PATH = os.getenv("MODEL_PATH", "models/qwen2.5-coder-14b-instruct-q5_k_m.gguf")
MODEL_N_CTX = int(os.getenv("MODEL_N_CTX", "8192"))
MODEL_N_THREADS = int(os.getenv("MODEL_N_THREADS", "4"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.4"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "300"))
PLATFORM_LIMIT = 300
PAT = os.getenv("PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
BOT_DID = os.getenv("BOT_DID")
BOT_HANDLE = os.getenv("BOT_HANDLE")
BOT_PASSWORD = os.getenv("BOT_PASSWORD")
OWNER_DID = os.getenv("OWNER_DID")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
SEARCH_TIMEOUT = 20
STATE_FILE = os.getenv("STATE_FILE", "state/runtime.json")
CONTEXT_SLOT_COUNT = int(os.getenv("CONTEXT_SLOT_COUNT", "50"))

def load_prompts():
    prompts = {}
    prompts_dir = os.path.join(os.path.dirname(__file__), "..", "prompts")
    if os.path.exists(prompts_dir):
        for f in os.listdir(prompts_dir):
            if f.endswith(".yaml"):
                with open(os.path.join(prompts_dir, f)) as file:
                    prompts.update(yaml.safe_load(file))
    return prompts
