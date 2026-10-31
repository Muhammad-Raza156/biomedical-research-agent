"""Re-run ingestion of corpus v1 (staged, so no NCBI calls) and assert identical graph counts."""

import json
import uuid

import pytest
import redis.asyncio as aioredis
from shared.contracts import (
    CORPUS_STATS_KEY,
    INGESTION_WORKFLOW,
    NCBI_REQUEST_LOG_KEY,
    TASK_QUEUE_INGEST,
    IngestionInput,
)
from temporalio.client import Client

pytestmark = pytest.mark.integration


async def test_second_ingestion_gives_identical_counts_and_no_ncbi_calls():
    redis = aioredis.from_url("redis://localhost:16379/0", decode_responses=True)
    before = json.loads(await redis.get(CORPUS_STATS_KEY))
    requests_before = await redis.xlen(NCBI_REQUEST_LOG_KEY)

    client = await Client.connect("localhost:7233")
    result = await client.execute_workflow(INGESTION_WORKFLOW, IngestionInput(corpus_version="v1"),
                                           id=f"ingest-v1-idempotency-{uuid.uuid4().hex[:6]}",
                                           task_queue=TASK_QUEUE_INGEST)
    after = result["corpus_stats"]

    assert after["node_counts"] == before["node_counts"]
    assert after["relationship_counts"] == before["relationship_counts"]
    assert after["cites_by_source"] == before["cites_by_source"]
    assert await redis.xlen(NCBI_REQUEST_LOG_KEY) == requests_before      # nothing refetched
    await redis.aclose()
