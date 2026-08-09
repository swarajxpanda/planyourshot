from langchain_ollama import ChatOllama
from planyourshot.config import LLM_MODEL

def get_llm(**kwargs) -> ChatOllama:
    return ChatOllama(model=LLM_MODEL, **kwargs)