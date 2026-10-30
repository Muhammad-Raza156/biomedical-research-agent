"""Workflow logic with mocked activities in Temporal's time-skipping test server (downloads once)."""

import uuid

import pytest
from shared.contracts import IngestionInput
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from activities.types import BatchInput, EventInput, StageResult
from workflows.ingestion import IngestionWorkflow

QUEUE = "test-ingestion"


def mocks(log: list, fail_link: bool = False):
    @activity.defn(name="discover")
    async def discover(version: str) -> StageResult:
        log.append("discover")
        return StageResult(items=5, batches=3)

    @activity.defn(name="fetch_batch")
    async def fetch_batch(inp: BatchInput) -> StageResult:
        log.append(f"fetch{inp.index}")
        return StageResult(items=2, batches=1)

    @activity.defn(name="link_all")
    async def link_all(version: str) -> StageResult:
        log.append("link")
        if fail_link:
            raise RuntimeError("NCBI unreachable")
        return StageResult(items=5, batches=3)

    @activity.defn(name="enrich")
    async def enrich(version: str) -> StageResult:
        log.append("enrich")
        return StageResult(items=10)

    @activity.defn(name="prepare_load")
    async def prepare_load(version: str) -> StageResult:
        log.append("prepare")
        return StageResult(items=5, batches=2)

    @activity.defn(name="load_reference_data")
    async def load_reference_data(version: str) -> StageResult:
        log.append("reference")
        return StageResult()

    @activity.defn(name="load_batch")
    async def load_batch(inp: BatchInput) -> StageResult:
        log.append(f"load{inp.index}")
        return StageResult(items=3 if inp.index == 0 else 2)

    @activity.defn(name="finalize")
    async def finalize(version: str) -> dict:
        log.append("finalize")
        return {"papers": 5}

    @activity.defn(name="publish_event")
    async def publish_event(inp: EventInput) -> None:
        log.append(f"event:{inp.type}")

    return [
        discover,
        fetch_batch,
        link_all,
        enrich,
        prepare_load,
        load_reference_data,
        load_batch,
        finalize,
        publish_event,
    ]


async def run(inp: IngestionInput, log: list, fail_link: bool = False):
    async with (
        await WorkflowEnvironment.start_time_skipping() as env,
        Worker(
            env.client,
            task_queue=QUEUE,
            workflows=[IngestionWorkflow],
            activities=mocks(log, fail_link),
        ),
    ):
        return await env.client.execute_workflow(
            IngestionWorkflow.run,
            inp,
            id=f"ingest-test-{uuid.uuid4()}",
            task_queue=QUEUE,
        )


async def test_stages_run_in_order_and_result_is_finalize_output():
    log: list = []
    result = await run(IngestionInput(corpus_version="v1"), log)
    work = [entry for entry in log if not entry.startswith("event:")]
    assert work[0] == "discover"
    assert sorted(work[1:4]) == ["fetch0", "fetch1", "fetch2"]  # fanned out
    assert work[4:] == [
        "link",
        "enrich",
        "prepare",
        "reference",
        "load0",
        "load1",
        "finalize",
    ]
    assert result == {"papers": 5}
    assert log.count("event:stage_started") == 5


async def test_failure_publishes_error_event_and_fails_cleanly():
    log: list = []
    with pytest.raises(WorkflowFailureError):
        await run(IngestionInput(corpus_version="v1"), log, fail_link=True)
    assert "event:error" in log
    assert "enrich" not in log
    assert log.count("link") == 8  # NCBI_RETRY.maximum_attempts, then gave up


async def test_continue_as_new_carries_on_to_completion():
    log: list = []
    result = await run(
        IngestionInput(corpus_version="v1", continue_as_new_events=1), log
    )
    assert result == {"papers": 5}
    assert (
        log.count("discover") == 1
    )  # completed stages are not repeated after continue_as_new               # NCBI_RETRY.maximum_attempts, then gave up
