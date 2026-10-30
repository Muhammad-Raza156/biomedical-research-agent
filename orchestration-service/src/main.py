"""Worker process: builds clients once, runs Temporal workers, auto-starts ingestion."""

from __future__ import annotations

import asyncio
import logging

import httpx
import redis.asyncio as aioredis
from shared.contracts import NCBI_BUCKET_KEY, NCBI_REQUEST_LOG_KEY, TASK_QUEUE_INGEST
from temporalio.worker import Worker

from activities.ingestion import IngestionActivities
from bootstrap import connect_temporal, ensure_ingestion_started
from clients.events import EventPublisher
from clients.mesh import MESH_BUCKET_KEY, MeshClient
from clients.ncbi import NcbiClient
from clients.rate_limiter import RedisTokenBucket
from config import Settings
from graph.driver import make_driver
from workflows.ingestion import IngestionWorkflow


async def main() -> None:
    """Wire dependencies and run until the container stops."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = Settings.from_env()
    client = await connect_temporal(settings.temporal_address, settings.temporal_namespace)
    redis = aioredis.from_url(settings.redis_url)
    http = httpx.AsyncClient()
    driver = make_driver(settings.graph_uri, settings.graph_user, settings.graph_password)

    ncbi_limiter = RedisTokenBucket(redis, NCBI_BUCKET_KEY, settings.ncbi_rate, log_key=NCBI_REQUEST_LOG_KEY)
    ncbi = NcbiClient(http, ncbi_limiter, tool=settings.ncbi_tool, email=settings.ncbi_email,
                      api_key=settings.ncbi_api_key)
    mesh = MeshClient(http, RedisTokenBucket(redis, MESH_BUCKET_KEY, 5.0))
    events = EventPublisher(redis, settings.stream_maxlen, settings.stream_ttl_seconds)
    ingestion = IngestionActivities(settings, ncbi, mesh, driver, events)

    ingest_worker = Worker(client, task_queue=TASK_QUEUE_INGEST, workflows=[IngestionWorkflow],
                           activities=ingestion.all(), max_concurrent_activities=8)
    await ensure_ingestion_started(client, settings.corpus_version, settings.ingest_continue_as_new_events)
    try:
        await ingest_worker.run()
    finally:
        await http.aclose()
        await driver.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
