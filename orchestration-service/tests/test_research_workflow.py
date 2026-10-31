"""ResearchWorkflow with mocked activities on a local Temporal dev server (downloaded once).

The time-skipping test server does not fully support workflow *updates*, so these tests use
WorkflowEnvironment.start_local() and a short real clarification timeout instead.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager

import pytest
from shared.contracts import CLARIFY_TIMEOUT_CHOICE, ResearchInput
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowUpdateFailedError
from temporalio.exceptions import ApplicationError
from temporalio.service import RPCError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities.research import AgentRunInput, AgentRunResult, TerminalInput
from workflows.research import ResearchWorkflow

QUEUE = "test-research"
OPTIONS = [{"id": "orcid:A", "label": "Smith John", "papers": 4}, {"id": "name:smith|j:2/1", "label": "Smith Jane", "papers": 2}]


def mocks(log: list, ambiguous: bool, hang: bool = False, fail: bool = False):
    @activity.defn(name="run_agent")
    async def run_agent(inp: AgentRunInput) -> AgentRunResult:
        log.append(("run_agent", inp.resume_value))
        if fail:
            raise ApplicationError("graph exploded", non_retryable=True)
        if hang:
            while True:                      # a long run that only ends by cancellation
                activity.heartbeat()
                await asyncio.sleep(0.5)
        if ambiguous and inp.resume_value is None:
            return AgentRunResult(status="awaiting_input", mention="Smith J", options=OPTIONS)
        return AgentRunResult(status="completed", answer={"answer": f"used {inp.resume_value}"})

    @activity.defn(name="publish_terminal")
    async def publish_terminal(inp: TerminalInput) -> None:
        log.append(("terminal", inp.status))

    return [run_agent, publish_terminal]


async def wait_for_status(handle, wanted: str) -> dict:
    for _ in range(100):
        status = await handle.query("status")
        if status["status"] == wanted:
            return status
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached {wanted}")


@asynccontextmanager
async def research_worker(log: list, **behaviour):
    """A local Temporal server plus a worker running ResearchWorkflow with the mocked activities."""
    async with (await WorkflowEnvironment.start_local() as env,
                Worker(env.client, task_queue=QUEUE, workflows=[ResearchWorkflow], activities=mocks(log, **behaviour))):
        yield env.client


async def start(client, query_id: str, **extra):
    return await client.start_workflow(ResearchWorkflow.run, ResearchInput(query_id=query_id, question="?", **extra),
                                       id=f"r-{uuid.uuid4()}", task_queue=QUEUE)


async def test_completes_without_clarification():
    log: list = []
    async with research_worker(log, ambiguous=False) as client:
        assert await (await start(client, "q1")).result() == {"answer": "used None"}


async def test_clarify_update_resumes_with_choice_and_rejects_bad_calls():
    log: list = []
    async with research_worker(log, ambiguous=True) as client:
        handle = await start(client, "q2")
        status = await wait_for_status(handle, "awaiting_input")
        assert status["clarification"]["mention"] == "Smith J"
        with pytest.raises(WorkflowUpdateFailedError):
            await handle.execute_update("clarify", "not-an-option")
        assert await handle.execute_update("clarify", "name:smith|j:2/1") == "accepted"
        assert await handle.result() == {"answer": "used name:smith|j:2/1"}
        with pytest.raises((WorkflowUpdateFailedError, RPCError)):  # workflow finished: server refuses
            await handle.execute_update("clarify", "orcid:A")


async def test_clarification_timeout_continues_with_sentinel():
    log: list = []
    async with research_worker(log, ambiguous=True) as client:
        result = await (await start(client, "q3", clarify_timeout_seconds=2)).result()
    assert result == {"answer": f"used {CLARIFY_TIMEOUT_CHOICE}"}


async def test_cancel_while_waiting_publishes_cancelled_terminal_event():
    log: list = []
    async with research_worker(log, ambiguous=True) as client:
        handle = await start(client, "q4")
        await wait_for_status(handle, "awaiting_input")
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    assert ("terminal", "cancelled") in log


async def test_cancel_during_agent_run_publishes_cancelled_terminal_event():
    log: list = []
    async with research_worker(log, ambiguous=False, hang=True) as client:
        handle = await start(client, "q5")
        await asyncio.sleep(1)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await handle.result()
    assert ("terminal", "cancelled") in log


async def test_agent_failure_publishes_failed_terminal_event_and_fails_the_workflow():
    log: list = []
    async with research_worker(log, ambiguous=False, fail=True) as client:
        handle = await start(client, "q6")
        with pytest.raises(WorkflowFailureError):
            await handle.result()
        assert (await handle.query("status"))["status"] == "failed"
    assert ("terminal", "failed") in log and ("terminal", "cancelled") not in log
