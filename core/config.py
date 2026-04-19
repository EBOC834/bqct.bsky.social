# core/config.py
import os
import yaml
from llama_cpp import Llama

MODEL_PATH = os.getenv("MODEL_PATH", "models/qwen2.5-coder-14b-instruct-q5_k_m.gguf")
MODEL_N_CTX = int(os.getenv("MODEL_N_CTX", "8192"))
MODEL_N_THREADS = int(os.getenv("MODEL_N_THREADS", "4"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.4"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "300"))
PLATFORM_LIMIT = 300
PAT = os.getenv("PAT")
GITHUB_REPOSITORY = os.getenv("GITHUB_REPOSITORY")
BOT_DID = os.getenv("BOT_DID")

def load_prompts():
    prompts = {}
    for f in os.listdir("prompts"):
        if f.endswith(".yaml"):
            with open(os.path.join("prompts", f)) as file:
                prompts.update(yaml.safe_load(file))
    return prompts
