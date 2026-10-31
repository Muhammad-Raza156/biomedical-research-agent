"""WorkflowGateway against a local Temporal dev server: real errors map to NotFound / Conflict / Unavailable."""

import uuid

import pytest
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from gateway import Conflict, NotFound, Unavailable, WorkflowGateway

QUEUE = "test-gateway"


@workflow.defn(name="Waiter")
class Waiter:
    """Waits for one `clarify` update; the validator only accepts 'ok'."""

    def __init__(self) -> None:
        self._choice: str | None = None

    @workflow.run
    async def run(self) -> str:
        await workflow.wait_condition(lambda: self._choice is not None)
        return self._choice or ""

    @workflow.update(name="clarify")
    async def clarify(self, choice: str) -> str:
        self._choice = choice
        return "accepted"

    @clarify.validator
    def _validate(self, choice: str) -> None:
        if choice != "ok":
            raise ValueError("not an option")


async def test_gateway_maps_temporal_errors():
    async with await WorkflowEnvironment.start_local() as env:
        gateway = WorkflowGateway(env.client.service_client.config.target_host, env.client.namespace)
        with pytest.raises(Unavailable):
            await gateway.status("anything")                       # not connected yet → 503
        assert await gateway.healthy() is False
        await gateway.connect_forever()
        assert await gateway.healthy() is True

        with pytest.raises(NotFound):
            await gateway.status("research-nope")

        async with Worker(env.client, task_queue=QUEUE, workflows=[Waiter]):
            wf_id = f"w-{uuid.uuid4()}"
            handle = await env.client.start_workflow("Waiter", id=wf_id, task_queue=QUEUE)
            assert await gateway.status(wf_id) == "RUNNING"
            with pytest.raises(Conflict, match="not an option"):  # validator rejected → 409
                await gateway.clarify(wf_id, "bad")
            await gateway.clarify(wf_id, "ok")
            assert await handle.result() == "ok"
            assert await gateway.status(wf_id) == "COMPLETED"
            with pytest.raises(Conflict):                            # finished: server refuses (RPCError) → 409
                await gateway.clarify(wf_id, "ok")
