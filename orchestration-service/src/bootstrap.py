"""Start-up helpers: resilient Temporal connection and exactly-once ingestion start."""

from __future__ import annotations

import asyncio
import logging

from shared.contracts import (
    INGESTION_WORKFLOW,
    TASK_QUEUE_INGEST,
    IngestionInput,
    ingest_workflow_id,
)
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

log = logging.getLogger(__name__)


async def connect_temporal(
    address: str, namespace: str, max_delay: float = 30.0
) -> Client:
    """Connect to Temporal, retrying with exponential back-off while the server starts."""
    delay = 1.0
    while True:
        try:
            return await Client.connect(address, namespace=namespace)
        except Exception as exc:  # noqa: BLE001 - any failure here means "not up yet"; keep trying
            log.warning(
                "temporal not reachable at %s (%s); retrying in %.0fs",
                address,
                exc,
                delay,
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)


async def ensure_ingestion_started(
    client: Client, corpus_version: str, continue_as_new_events: int
) -> str:
    """Start IngestionWorkflow for this corpus version unless one is running or completed."""
    run_id = ingest_workflow_id(corpus_version)
    try:
        await client.start_workflow(
            INGESTION_WORKFLOW,
            IngestionInput(
                corpus_version=corpus_version,
                continue_as_new_events=continue_as_new_events,
            ),
            id=run_id,
            task_queue=TASK_QUEUE_INGEST,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
        )
        log.info("ingestion %s started or already running", run_id)
    except WorkflowAlreadyStartedError:
        log.info("ingestion %s already completed; not starting again", run_id)
    return run_id
