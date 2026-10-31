"""ResearchWorkflow: durable wrapper around one agent run (Option A). Deterministic; no I/O here."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from shared.contracts import (
        CLARIFY_TIMEOUT_CHOICE,
        CLARIFY_UPDATE,
        RESEARCH_WORKFLOW,
        STATUS_QUERY,
        QueryStatus,
        ResearchInput,
    )

    from activities.research import (
        AgentRunInput,
        AgentRunResult,
        ResearchActivities,
        TerminalInput,
    )

# The agent activity: long (LLM + graph), heartbeats every 10 s; retried after crashes.
AGENT_RETRY = RetryPolicy(initial_interval=timedelta(seconds=5), backoff_coefficient=2.0,
                          maximum_interval=timedelta(minutes=1), maximum_attempts=5)
# Terminal event publishing: tiny Redis write; retry a few times quickly.
TERMINAL_RETRY = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_attempts=5)


@workflow.defn(name=RESEARCH_WORKFLOW)
class ResearchWorkflow:
    """Runs the agent; waits durably for clarification; publishes terminal errors on failure/cancel."""

    def __init__(self) -> None:
        self._status: str = QueryStatus.RUNNING.value
        self._clarification: dict | None = None
        self._choice: str | None = None

    @workflow.run
    async def run(self, inp: ResearchInput) -> dict:
        resume: str | None = None
        try:
            while True:
                result: AgentRunResult = await workflow.execute_activity_method(
                    ResearchActivities.run_agent, AgentRunInput(inp.query_id, inp.question, resume),
                    start_to_close_timeout=timedelta(minutes=30), heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=AGENT_RETRY)
                if result.status == "completed":
                    self._status = QueryStatus.COMPLETED.value
                    return result.answer or {}
                self._status = QueryStatus.AWAITING_INPUT.value
                self._clarification = {"mention": result.mention, "options": result.options}
                try:
                    await workflow.wait_condition(lambda: self._choice is not None,
                                                  timeout=timedelta(seconds=inp.clarify_timeout_seconds))
                    resume = self._choice
                except TimeoutError:
                    resume = CLARIFY_TIMEOUT_CHOICE
                self._choice, self._clarification = None, None
                self._status = QueryStatus.RUNNING.value
        except asyncio.CancelledError:                      # cancelled while waiting (no activity running)
            await self._cancelled(inp.query_id)
            raise
        except ActivityError as err:
            if isinstance(err.cause, CancelledError):       # cancelled while run_agent was running
                await self._cancelled(inp.query_id)
                raise asyncio.CancelledError("query cancelled") from err
            self._status = QueryStatus.FAILED.value
            reason = f"agent failed: {err.cause or err}"
            await self._terminal(inp.query_id, "failed", reason)
            raise ApplicationError(reason, non_retryable=True) from err

    async def _cancelled(self, query_id: str) -> None:
        self._status = QueryStatus.CANCELLED.value
        await self._terminal(query_id, "cancelled", "query cancelled by the user")

    async def _terminal(self, query_id: str, status: str, reason: str) -> None:
        await workflow.execute_activity_method(
            ResearchActivities.publish_terminal, TerminalInput(query_id, status, reason),
            start_to_close_timeout=timedelta(seconds=15), retry_policy=TERMINAL_RETRY)

    @workflow.update(name=CLARIFY_UPDATE)
    async def clarify(self, choice: str) -> str:
        """Deliver the user's choice (validated first)."""
        self._choice = choice
        return "accepted"

    @clarify.validator
    def _validate_clarify(self, choice: str) -> None:
        """Reject (→ HTTP 409) unless we are waiting and the choice is one of the offered options."""
        if self._status != QueryStatus.AWAITING_INPUT.value or not self._clarification:
            raise ValueError(f"query is {self._status}, not awaiting input")
        allowed = {o["id"] for o in self._clarification["options"]} | {o["label"] for o in self._clarification["options"]}
        if choice not in allowed:
            raise ValueError(f"'{choice}' is not one of the offered options")

    @workflow.query(name=STATUS_QUERY)
    def status(self) -> dict:
        """Current status and, when waiting, the clarification question."""
        return {"status": self._status, "clarification": self._clarification}
