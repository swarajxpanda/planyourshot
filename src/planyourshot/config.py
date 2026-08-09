import os
from dotenv import load_dotenv

load_dotenv()

if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError("GEMINI_API_KEY is missing — add it to your .env file.")

LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
CHROMA_DIR = os.getenv("CHROMA_DIR", "data/chroma")
KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "data/knowledge")
    