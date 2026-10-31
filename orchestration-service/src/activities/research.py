"""Option A: the whole LangGraph run executes inside this heartbeating activity."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from shared.contracts import (
    EventType,
    StreamEvent,
    answer_key,
    meta_json,
    thinking_stream_key,
)
from temporalio import activity

from agent.deps import AgentDeps, AgentSettings
from agent.graph import build_graph
from agent.ports import GraphReader, LLMClient
from agent.runner import run_once
from clients.events import EventPublisher

HEARTBEAT_EVERY_SECONDS = 10


@dataclass
class AgentRunInput:
    """One attempt to advance a query (resume_value is the clarification answer, if any)."""

    query_id: str
    question: str
    resume_value: str | None = None


@dataclass
class AgentRunResult:
    """Paused for clarification, or completed with the final answer."""

    status: str
    mention: str = ""
    options: list[dict] = field(default_factory=list)
    answer: dict | None = None


@dataclass
class TerminalInput:
    """Terminal failure/cancellation to publish."""

    query_id: str
    status: str
    reason: str


class RedisThinkingSink:
    """agent.ports.ThinkingSink → Redis stream thinking:{query_id}."""

    def __init__(self, publisher: EventPublisher, query_id: str) -> None:
        self._publisher = publisher
        self._stream = thinking_stream_key(query_id)

    async def emit(self, event: StreamEvent) -> None:
        await self._publisher.publish(self._stream, event)


class ResearchActivities:
    """Shared, process-wide dependencies; one graph is built per activity attempt."""

    def __init__(self, llm: LLMClient, graph_reader: GraphReader, checkpointer, publisher: EventPublisher,
                 settings: AgentSettings, answer_ttl_seconds: int) -> None:
        self._llm = llm
        self._graph = graph_reader
        self._checkpointer = checkpointer
        self._publisher = publisher
        self._settings = settings
        self._ttl = answer_ttl_seconds

    def all(self) -> list:
        """Bound activity methods to register with the research Worker."""
        return [self.run_agent, self.publish_terminal]

    @staticmethod
    async def _heartbeat_forever() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(HEARTBEAT_EVERY_SECONDS)

    @activity.defn
    async def run_agent(self, inp: AgentRunInput) -> AgentRunResult:
        """Advance the graph for this query until it finishes or needs the user."""
        stream = thinking_stream_key(inp.query_id)
        last = await self._publisher.last_event(stream)
        deps = AgentDeps(llm=self._llm, graph=self._graph, sink=RedisThinkingSink(self._publisher, inp.query_id),
                         settings=self._settings, suppress_upto=last.step if last else 0)
        heartbeat = asyncio.create_task(self._heartbeat_forever())
        try:
            outcome = await run_once(build_graph(deps, self._checkpointer), deps, inp.query_id, inp.question,
                                     inp.resume_value)
        finally:
            heartbeat.cancel()

        if outcome.status == "awaiting_input":
            return AgentRunResult(status="awaiting_input", mention=outcome.interrupt["mention"],
                                  options=outcome.interrupt["options"])

        final = outcome.final
        await self._publisher.set_json(answer_key(inp.query_id),
                                       {"status": "completed", "query_id": inp.query_id,
                                        "question": inp.question, **final}, self._ttl)
        if not (last and last.type == EventType.ANSWER):   # never publish the answer twice
            step = max(outcome.step, await self._publisher.last_step(stream)) + 1
            await self._publisher.publish(stream, StreamEvent(
                step=step, node="answer", type=EventType.ANSWER, content=final["answer"],
                meta=meta_json(total_tokens=final["total_tokens"], estimated_cost_usd=final["estimated_cost_usd"],
                               claims_checked=final["claims_checked"], claims_passed=final["claims_passed"],
                               claims_dropped=final["claims_dropped"])))
        return AgentRunResult(status="completed", answer=final)

    @activity.defn
    async def publish_terminal(self, inp: TerminalInput) -> None:
        """Publish the terminal `error` event (failed / cancelled) and persist the status."""
        stream = thinking_stream_key(inp.query_id)
        last = await self._publisher.last_event(stream)
        if last and last.is_terminal:
            return
        await self._publisher.set_json(answer_key(inp.query_id),
                                       {"status": inp.status, "query_id": inp.query_id, "error": inp.reason},
                                       self._ttl)
        await self._publisher.publish(stream, StreamEvent(
            step=(last.step if last else 0) + 1, node="workflow", type=EventType.ERROR,
            content=inp.reason, meta=meta_json(reason=inp.status)))
