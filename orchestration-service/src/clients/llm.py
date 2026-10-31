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
                 max_retries: int = 4) -> None:
        kwargs: dict = {"temperature": temperature, "max_retries": max_retries}
        if model.startswith("ollama:"):
            kwargs["base_url"] = ollama_base_url
        self._model = init_chat_model(model, **kwargs)
        self._limiter = limiter
        self.model_name = model

    async def structured(self, system: str, user: str, schema):
        """Ask for `schema`; return (parsed object, token usage)."""
        await self._limiter.acquire("llm")
        runnable = self._model.with_structured_output(schema, include_raw=True)
        out = await runnable.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        if out.get("parsing_error") or out.get("parsed") is None:
            raise LLMOutputError(f"{schema.__name__}: {out.get('parsing_error')}")
        usage = getattr(out["raw"], "usage_metadata", None) or {}
        return out["parsed"], TokenUsage(usage.get("input_tokens", 0), usage.get("output_tokens", 0))
