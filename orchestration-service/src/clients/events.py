"""Publishes stream events (thinking + ingest) and small JSON documents to Redis."""

from __future__ import annotations

import json

from redis.asyncio import Redis

from shared.contracts import StreamEvent, ingest_seq_key, ingest_stream_key


class EventPublisher:
    """XADD with retention (MAXLEN ~ and TTL after the terminal event)."""

    def __init__(self, redis: Redis, maxlen: int, ttl_seconds: int) -> None:
        self._redis = redis
        self._maxlen = maxlen
        self._ttl = ttl_seconds

    async def publish(self, stream_key: str, event: StreamEvent) -> str:
        """Append one event; after a terminal event the whole stream gets a TTL."""
        entry_id = await self._redis.xadd(
            stream_key, event.to_redis(), maxlen=self._maxlen, approximate=True
        )
        if event.is_terminal:
            await self._redis.expire(stream_key, self._ttl)
        return entry_id.decode() if isinstance(entry_id, bytes) else entry_id

    async def publish_ingest(
        self, run_id: str, type: str, content: str, meta: str = ""
    ) -> str:
        """Ingestion progress event with a cross-worker monotonic step number."""
        step = await self._redis.incr(ingest_seq_key(run_id))
        event = StreamEvent(
            step=step, node="ingestion", type=type, content=content, meta=meta
        )
        return await self.publish(ingest_stream_key(run_id), event)

    async def last_event(self, stream_key: str) -> StreamEvent | None:
        """Newest event in a stream, or None when the stream is empty."""
        newest = await self._redis.xrevrange(stream_key, count=1)
        return StreamEvent.from_redis(newest[0][1]) if newest else None

    async def last_step(self, stream_key: str) -> int:
        """Step of the newest event in a stream (0 when empty) — used to suppress duplicates."""
        event = await self.last_event(stream_key)
        return event.step if event else 0

    async def set_json(
        self, key: str, value: object, ttl_seconds: int | None = None
    ) -> None:
        """Store a JSON document (final answer, ingest result, corpus stats)."""
        await self._redis.set(key, json.dumps(value, default=str), ex=ttl_seconds)
