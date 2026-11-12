import os
from dotenv import load_dotenv

load_dotenv()

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()  # "ollama" | "gemini"
LLM_MODEL = os.getenv("LLM_MODEL", "gemma3" if LLM_PROVIDER == "ollama" else "gemini-3.6-flash")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# Ollama's default context window is only a few thousand tokens and it
# truncates silently, so a screenplay would be cut off. Ask for more.
OLLAMA_NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "16384"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHROMA_DIR = os.getenv("CHROMA_DIR", "data/chroma")
KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "data/knowledge")


def require_gemini_key() -> str:
    """Checked when a Gemini model is actually built, so the web app and the
    Ollama path can start without a key."""
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is missing — add it to your .env file.")
    return GEMINI_API_KEY
