"""FastAPI entrypoint: only starts and observes work. No agent, ingestion or Cypher logic here."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from config import ApiSettings
from gateway import Conflict, NotFound, Unavailable, WorkflowGateway
from routes import corpus, health, ingest, queries


def make_redis(settings: ApiSettings) -> aioredis.Redis:
    """Redis client for the API (lazy: connects on first command).

    The socket timeout must outlast the longest XREAD BLOCK, or redis-py's 5 s default ends every SSE stream.
    """
    return aioredis.from_url(settings.redis_url, decode_responses=True,
                             socket_timeout=settings.sse_block_ms / 1000 + 5, socket_connect_timeout=5)


def create_app(settings: ApiSettings | None = None, gateway=None, redis=None) -> FastAPI:
    """Build the app; tests pass fakes for gateway/redis."""
    settings = settings or ApiSettings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.redis = redis or make_redis(settings)
        app.state.gateway = gateway or WorkflowGateway(settings.temporal_address, settings.temporal_namespace)
        connector = None if gateway else asyncio.create_task(app.state.gateway.connect_forever())
        yield
        if connector:
            connector.cancel()
        await app.state.redis.aclose()

    app = FastAPI(title="Biomedical Research Agent API", version="1.0.0", lifespan=lifespan)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc) or "not found"}, status_code=404)

    @app.exception_handler(Conflict)
    async def _conflict(request: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(Unavailable)
    async def _unavailable(request: Request, exc: Unavailable) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    for router in (ingest.router, queries.router, corpus.router, health.router):
        app.include_router(router)
    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()
