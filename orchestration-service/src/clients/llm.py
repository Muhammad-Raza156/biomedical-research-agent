"""Any chat model via LangChain's init_chat_model, chosen by LLM_MODEL in .env."""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.messages import HumanMessage, SystemMessage

from agent.ports import TokenUsage
from clients.ncbi import Limiter


class LLMOutputError(Exception):
    """The model's output did not fit the schema (retried by the activity, then fails the run)."""


class LangChainLLM:
    """Structured output + token usage, rate-limited across workers by a shared token bucket."""

    def __init__(self, model: str, temperature: float, limiter: Limiter, ollama_base_url: str = "",
                 max_retries: int = 4, timeout: float = 60.0) -> None:
        # timeout: a stalled connection fails (and is retried) instead of hanging the run forever
        kwargs: dict = {"temperature": temperature, "max_retries": max_retries, "timeout": timeout}
        if model.startswith("ollama:"):
            kwargs["base_url"] = ollama_base_url
        self._model = init_chat_model(model, **kwargs)
        self._limiter = limiter
        self.model_name = model

    async def structured(self, system: str, user: str, schema, attempts: int = 2):
        """Ask for `schema`; return (parsed object, token usage). A reply that misses the schema is asked again once."""
        # json_schema: the API constrains the reply to the schema (tool calling broke on Groq's gpt-oss)
        runnable = self._model.with_structured_output(schema, method="json_schema", include_raw=True)
        error = None
        for _ in range(attempts):
            await self._limiter.acquire("llm")
            try:
                out = await runnable.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
            except Exception as exc:  # Groq validates JSON server-side and answers 400 output_parse_failed
                if "output_parse_failed" not in str(exc):
                    raise
                error = exc
                continue
            error = out.get("parsing_error")
            if not error and out.get("parsed") is not None:
                usage = getattr(out["raw"], "usage_metadata", None) or {}
                return out["parsed"], TokenUsage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
        raise LLMOutputError(f"{schema.__name__}: {error}")
