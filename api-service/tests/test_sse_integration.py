"""Needs Redis from the docker stack on localhost:16379."""

import asyncio

import httpx
import pytest
import redis.asyncio as aioredis

from config import ApiSettings
from main import create_app, make_redis
from tests.fakes import FakeGateway, event_fields

pytestmark = pytest.mark.integration
KEY = "thinking:it1"


async def test_five_subscribers_each_get_every_event_in_order():
    redis = aioredis.from_url("redis://localhost:16379/15", decode_responses=True)
    await redis.delete(KEY)
    gateway = FakeGateway()
    gateway.statuses["research-it1"] = "RUNNING"
    app = create_app(ApiSettings.from_env({"SSE_BLOCK_MS": "200"}), gateway=gateway, redis=redis)

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://api") as client:
            async def subscribe() -> list[str]:
                response = await client.get("/queries/it1/stream", timeout=30)
                return [line[4:] for line in response.text.split("\n") if line.startswith("id: ")]

            subscribers = [asyncio.create_task(subscribe()) for _ in range(5)]
            await asyncio.sleep(0.3)                       # everyone is connected and waiting
            ids = [await redis.xadd(KEY, event_fields(step, "reasoning")) for step in range(1, 20)]
            ids.append(await redis.xadd(KEY, event_fields(20, "answer")))
            results = await asyncio.gather(*subscribers)
    assert all(result == ids for result in results)
    await redis.delete(KEY)


async def test_api_redis_client_survives_a_block_longer_than_the_default_socket_timeout():
    # redis-py 8 defaults socket_timeout to 5 s; XREAD BLOCK 15000 used to raise TimeoutError and kill the stream
    settings = ApiSettings.from_env({"REDIS_URL": "redis://localhost:16379/15", "SSE_BLOCK_MS": "6000"})
    redis = make_redis(settings)
    try:
        assert await redis.xread({"thinking:never-written": "0-0"}, block=settings.sse_block_ms) == []
    finally:
        await redis.aclose()
