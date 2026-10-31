import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from agent.schemas import ObserveOutput
from clients.llm import LangChainLLM, LLMOutputError

RAW = AIMessage(content="", usage_metadata={"input_tokens": 321, "output_tokens": 45, "total_tokens": 366})
GOOD = {"raw": RAW, "parsed": ObserveOutput(summary="s", decision="next"), "parsing_error": None}
BAD = {"raw": RAW, "parsed": None, "parsing_error": ValueError("summary: Field required")}


class CountingLimiter:
    def __init__(self) -> None:
        self.calls = 0

    async def acquire(self, label: str = "") -> None:
        self.calls += 1


class ScriptedModel:
    """Stands in for a chat model: with_structured_output returns the scripted replies in order."""

    def __init__(self, replies: list[dict]) -> None:
        self.replies = list(replies)

    def with_structured_output(self, schema, method, include_raw):
        assert method == "json_schema" and include_raw
        return RunnableLambda(self._reply)

    def _reply(self, messages):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def make_llm(replies: list[dict], monkeypatch) -> tuple[LangChainLLM, CountingLimiter]:
    monkeypatch.setenv("GROQ_API_KEY", "not-a-real-key")
    limiter = CountingLimiter()
    llm = LangChainLLM("groq:openai/gpt-oss-120b", 0.0, limiter)
    llm._model = ScriptedModel(replies)
    return llm, limiter


async def test_returns_parsed_output_and_token_usage(monkeypatch):
    llm, limiter = make_llm([GOOD], monkeypatch)
    parsed, usage = await llm.structured("sys", "user", ObserveOutput)
    assert parsed.decision == "next" and (usage.input_tokens, usage.output_tokens) == (321, 45)
    assert limiter.calls == 1


async def test_a_reply_missing_a_field_is_asked_again_once(monkeypatch):
    llm, limiter = make_llm([BAD, GOOD], monkeypatch)
    parsed, _ = await llm.structured("sys", "user", ObserveOutput)
    assert parsed.summary == "s" and limiter.calls == 2       # the retry also goes through the rate limiter


async def test_two_bad_replies_raise(monkeypatch):
    llm, _ = make_llm([BAD, BAD], monkeypatch)
    with pytest.raises(LLMOutputError, match="ObserveOutput"):
        await llm.structured("sys", "user", ObserveOutput)


async def test_server_side_parse_failure_is_asked_again_but_other_errors_propagate(monkeypatch):
    for code in ("output_parse_failed", "json_validate_failed"):   # Groq uses both for a reply off the schema
        groq_400 = RuntimeError(f"Error code: 400 - {{'error': {{'code': '{code}'}}}}")
        llm, limiter = make_llm([groq_400, GOOD], monkeypatch)
        parsed, _ = await llm.structured("sys", "user", ObserveOutput)
        assert parsed.decision == "next" and limiter.calls == 2
    llm, _ = make_llm([RuntimeError("Error code: 401 - invalid api key")], monkeypatch)
    with pytest.raises(RuntimeError, match="401"):
        await llm.structured("sys", "user", ObserveOutput)
