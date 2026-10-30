"""Exactly-once ingestion start: a second start joins the running run, or leaves a completed one alone."""

from shared.contracts import INGESTION_WORKFLOW, TASK_QUEUE_INGEST, IngestionInput
from temporalio import workflow
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from bootstrap import ensure_ingestion_started


@workflow.defn(name=INGESTION_WORKFLOW)
class SlowIngestion:
    @workflow.run
    async def run(self, inp: IngestionInput) -> dict:
        await workflow.sleep(3600)  # stays "running" while we start it again
        return {}


@workflow.defn(name=INGESTION_WORKFLOW)
class QuickIngestion:
    @workflow.run
    async def run(self, inp: IngestionInput) -> dict:
        return {}


async def test_second_start_joins_the_running_run():
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(env.client, task_queue=TASK_QUEUE_INGEST, workflows=[SlowIngestion]),
    ):
        first = await ensure_ingestion_started(env.client, "v1", 2000)
        handle = env.client.get_workflow_handle("ingest-v1")
        before = (await handle.describe()).run_id
        second = await ensure_ingestion_started(env.client, "v1", 2000)  # e.g. worker-2 booting
        assert first == second == "ingest-v1"
        assert (await handle.describe()).run_id == before  # same run, not a new one


async def test_completed_run_is_not_restarted():
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(env.client, task_queue=TASK_QUEUE_INGEST, workflows=[QuickIngestion]),
    ):
        await ensure_ingestion_started(env.client, "v1", 2000)
        handle = env.client.get_workflow_handle("ingest-v1")
        await handle.result()  # wait until it has completed
        before = (await handle.describe()).run_id
        await ensure_ingestion_started(env.client, "v1", 2000)  # e.g. `make up` after a restart
        assert (await handle.describe()).run_id == before  # still the old, completed run
