import pytest
import redis.asyncio as aioredis

from clients.events import EventPublisher
from shared.contracts import StreamEvent, ingest_stream_key

pytestmark = pytest.mark.integration


@pytest.fixture
async def redis_client(local_env):
    client = aioredis.from_url(local_env["REDIS_URL"])
    await client.flushdb()
    yield client
    await client.flushdb()
    await client.aclose()


async def test_ingest_events_get_monotonic_steps(redis_client):
    pub = EventPublisher(redis_client, maxlen=100, ttl_seconds=60)
    await pub.publish_ingest("run1", "stage_started", "discover")
    await pub.publish_ingest("run1", "batch_fetched", "batch 0")
    entries = await redis_client.xrange(ingest_stream_key("run1"))
    steps = [StreamEvent.from_redis(fields).step for _, fields in entries]
    assert steps == [1, 2]
    assert await pub.last_step(ingest_stream_key("run1")) == 2


async def test_terminal_event_sets_ttl(redis_client):
    pub = EventPublisher(redis_client, maxlen=100, ttl_seconds=60)
    await pub.publish("s", StreamEvent(step=1, type="node_enter", content="x"))
    assert await redis_client.ttl("s") == -1
    await pub.publish("s", StreamEvent(step=2, type="answer", content="done"))
    assert 0 < await redis_client.ttl("s") <= 60


async def test_empty_stream_last_step_is_zero(redis_client):
    assert await EventPublisher(redis_client, 10, 60).last_step("missing") == 0
