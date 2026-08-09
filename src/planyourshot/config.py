import os
from dotenv import load_dotenv

load_dotenv()

if not os.getenv("GEMINI_API_KEY"):
    raise RuntimeError("GEMINI_API_KEY is missing — add it to your .env file.")

LLM_MODEL = os.getenv("LLM_MODEL", "gemini-3.6-flash")
    