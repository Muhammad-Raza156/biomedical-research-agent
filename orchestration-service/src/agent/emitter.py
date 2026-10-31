"""Thinking-event emission with checkpointed, never-duplicated step numbers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.contracts import EventType, StreamEvent, meta_json

from agent.ports import TokenUsage

if TYPE_CHECKING:
    from agent.deps import AgentDeps


class Emitter:
    """Numbers events from the state's last step; suppresses steps already published.

    After a resume, the first *new* event is preceded by one `checkpoint` event (deps.resume_note).
    """

    def __init__(self, deps: AgentDeps, node: str, start_step: int) -> None:
        self._deps = deps
        self._node = node
        self.step = start_step

    async def _send(self, type: str, content: str, meta: str) -> None:
        await self._deps.sink.emit(StreamEvent(step=self.step, node=self._node, type=type,
                                               content=content, meta=meta))

    async def emit(self, type: str, content: str, **meta: object) -> None:
        """Publish one event (unless this step number was already published before a resume)."""
        self.step += 1
        if self.step <= self._deps.suppress_upto:
            return
        if self._deps.resume_note:
            await self._send(EventType.CHECKPOINT, self._deps.resume_note, "")
            self._deps.resume_note = ""
            self.step += 1
        await self._send(type, content, meta_json(**meta) if meta else "")


def add_usage(tokens: dict[str, int] | None, usage: TokenUsage) -> dict[str, int]:
    """Return new running token totals (never mutate state in place)."""
    tokens = tokens or {"input": 0, "output": 0}
    return {"input": tokens["input"] + usage.input_tokens, "output": tokens["output"] + usage.output_tokens}
