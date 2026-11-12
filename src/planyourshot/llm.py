from planyourshot.config import (
    LLM_MODEL,
    LLM_PROVIDER,
    OLLAMA_BASE_URL,
    OLLAMA_NUM_CTX,
    require_gemini_key,
)


def get_llm(*, timeout: float | None = None, max_output_tokens: int | None = None, **kwargs):
    """One place that builds the chat model. LLM_PROVIDER picks Ollama (local,
    the default) or Gemini (hosted); both support .invoke() and .with_structured_output().

    `timeout` (seconds) and `max_output_tokens` are mapped to each provider's own option."""
    if LLM_PROVIDER == "ollama":
        from langchain_ollama import ChatOllama

        kwargs.setdefault("num_ctx", OLLAMA_NUM_CTX)
        if max_output_tokens:
            kwargs.setdefault("num_predict", max_output_tokens)
        if timeout:
            kwargs.setdefault("client_kwargs", {"timeout": timeout})  # passed to the httpx client
        return ChatOllama(model=LLM_MODEL, base_url=OLLAMA_BASE_URL, **kwargs)
    from langchain_google_genai import ChatGoogleGenerativeAI

    if max_output_tokens:
        kwargs.setdefault("max_output_tokens", max_output_tokens)
    if timeout:
        kwargs.setdefault("timeout", timeout)
        # The SDK retries 6 times by default, which would multiply the timeout.
        kwargs.setdefault("max_retries", 1)
    return ChatGoogleGenerativeAI(
        model=LLM_MODEL, google_api_key=require_gemini_key(), **kwargs
    )
