"""Global (cross-replica) rate limiting backed by a Redis token bucket."""

from __future__ import annotations

import asyncio
import socket
import time
from pathlib import Path

from redis.asyncio import Redis

_LUA = (Path(__file__).parent / "token_bucket.lua").read_text(encoding="utf-8")


class RedisTokenBucket:
    """Blocks callers until the shared bucket grants a token."""

    def __init__(
        self,
        redis: Redis,
        key: str,
        rate_per_second: float,
        capacity: float = 1.0,
        log_key: str | None = None,
        worker_id: str | None = None,
    ) -> None:
        self._redis = redis
        self._key = key
        self._rate = rate_per_second
        self._capacity = capacity
        self._log_key = log_key
        self._worker_id = worker_id or socket.gethostname()
        self._script = redis.register_script(_LUA)

    async def acquire(self, label: str = "") -> None:
        """Wait until a token is available, take it, and log the grant."""
        while True:
            wait_ms = int(
                await self._script(keys=[self._key], args=[self._rate, self._capacity])
            )
            if wait_ms == 0:
                break
            await asyncio.sleep(wait_ms / 1000)

        if self._log_key:
            await self._redis.xadd(
                self._log_key,
                {
                    "worker": self._worker_id,
                    "ts_ms": str(int(time.time() * 1000)),
                    "label": label,
                },
                maxlen=100_000,
                approximate=True,
            )
