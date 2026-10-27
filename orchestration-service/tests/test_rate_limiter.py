"""Integration test: needs Redis from the docker stack on localhost:16379."""

import asyncio
import time

import pytest
import redis.asyncio as aioredis

from clients.rate_limiter import RedisTokenBucket

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_client(local_env):
    client = aioredis.from_url(local_env["REDIS_URL"])
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_two_workers_share_one_limit(redis_client):
    # Two limiter objects = two worker replicas sharing one Redis bucket.
    a = RedisTokenBucket(redis_client, "test:bucket", rate_per_second=3, log_key="test:log", worker_id="A")
    b = RedisTokenBucket(redis_client, "test:bucket", rate_per_second=3, log_key="test:log", worker_id="B")
    granted: list[float] = []

    async def take(limiter: RedisTokenBucket) -> None:
        await limiter.acquire("esearch")
        granted.append(time.monotonic())

    start = time.monotonic()
    await asyncio.gather(*[take(a) for _ in range(6)], *[take(b) for _ in range(6)])
    elapsed = time.monotonic() - start

    # 12 tokens at 3/s with capacity 1 must take at least ~11/3 s.
    assert elapsed >= 3.4
    granted.sort()
    for i, t in enumerate(granted):
        in_window = [g for g in granted[i:] if g - t < 1.0]
        assert len(in_window) <= 3 + 1  # +1 tolerance for client-side timing jitter

    log = await redis_client.xrange("test:log")
    workers = {fields[b"worker"] for _, fields in log}
    assert len(log) == 12 and workers == {b"A", b"B"}
