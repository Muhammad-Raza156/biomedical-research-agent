"""Worker process: builds clients once, runs Temporal workers, auto-starts ingestion."""

from __future__ import annotations

import asyncio
import logging

import httpx
import redis.asyncio as aioredis
from shared.contracts import (
    LLM_BUCKET_KEY,
    NCBI_BUCKET_KEY,
    NCBI_REQUEST_LOG_KEY,
    TASK_QUEUE_INGEST,
    TASK_QUEUE_RESEARCH,
)
from temporalio.worker import Worker

from activities.ingestion import IngestionActivities
from activities.research import ResearchActivities
from agent.deps import AgentSettings
from bootstrap import connect_temporal, ensure_ingestion_started
from clients.checkpointer import make_checkpointer
from clients.events import EventPublisher
from clients.llm import LangChainLLM
from clients.mesh import MESH_BUCKET_KEY, MeshClient
from clients.ncbi import NcbiClient
from clients.rate_limiter import RedisTokenBucket
from config import Settings
from graph.driver import make_driver
from graph.repository import Neo4jGraphReader
from workflows.ingestion import IngestionWorkflow
from workflows.research import ResearchWorkflow


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

    checkpointer, pg_pool = await make_checkpointer(settings.postgres_dsn)
    reader_driver = make_driver(settings.graph_uri, settings.graph_reader_user, settings.graph_reader_password)
    llm = LangChainLLM(settings.llm_model, settings.llm_temperature,
                       RedisTokenBucket(redis, LLM_BUCKET_KEY, settings.llm_requests_per_minute / 60),
                       ollama_base_url=settings.ollama_base_url)
    agent_settings = AgentSettings(
        max_iterations=settings.agent_max_iterations,
        max_verifier_rejections=settings.agent_max_verifier_rejections,
        max_cypher_retries=settings.agent_max_cypher_retries,
        cypher_timeout_seconds=settings.cypher_timeout_seconds, cypher_max_limit=settings.cypher_max_limit,
        cutoff_year=settings.corpus_cutoff_year, price_input_per_mtok=settings.llm_price_input_per_mtok,
        price_output_per_mtok=settings.llm_price_output_per_mtok)
    research = ResearchActivities(llm, Neo4jGraphReader(reader_driver, settings.graph_database), checkpointer,
                                  events, agent_settings, settings.stream_ttl_seconds)
    # Separate queue and concurrency limit: ingestion can't starve the agent and vice versa.
    research_worker = Worker(client, task_queue=TASK_QUEUE_RESEARCH, workflows=[ResearchWorkflow],
                             activities=research.all(),
                             max_concurrent_activities=settings.research_max_concurrent_runs)

    await ensure_ingestion_started(client, settings.corpus_version, settings.ingest_continue_as_new_events)
    try:
        await asyncio.gather(ingest_worker.run(), research_worker.run())
    finally:
        await http.aclose()
        await driver.close()
        await reader_driver.close()
        await pg_pool.close()
        await redis.aclose()

if __name__ == "__main__":
    asyncio.run(main())
