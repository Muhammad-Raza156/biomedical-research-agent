"""In-memory stand-ins for Redis and the Temporal gateway."""

from __future__ import annotations

import asyncio
import json

from gateway import Conflict, NotFound, Unavailable


class FakeRedis:
    """Implements the handful of Redis commands the API uses (decode_responses=True style)."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.up = True
        self._seq = 0

    async def ping(self) -> bool:
        if not self.up:
            raise ConnectionError("redis down")
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def exists(self, key) -> int:
        return int(key in self.streams or key in self.kv)

    async def xadd(self, key, fields) -> str:
        self._seq += 1
        entry_id = f"{1000 + self._seq}-0"
        self.streams.setdefault(key, []).append((entry_id, {k: str(v) for k, v in fields.items()}))
        return entry_id

    @staticmethod
    def _after(a: str, b: str) -> bool:
        return tuple(map(int, a.split("-"))) > tuple(map(int, b.split("-")))

    async def xread(self, streams: dict, block: int | None = None, count: int | None = None):
        key, cursor = next(iter(streams.items()))
        entries = [e for e in self.streams.get(key, []) if self._after(e[0], cursor)][:count]
        if not entries:
            await asyncio.sleep(0.01)
            return []
        return [(key, entries)]

    async def xrevrange(self, key, count: int | None = None):
        return list(reversed(self.streams.get(key, [])))[:count]

    async def aclose(self) -> None:
        return None


class FakeGateway:
    """Scriptable Temporal gateway."""

    def __init__(self) -> None:
        self.connected = True
        self.statuses: dict[str, str] = {}
        self.queries: dict[str, dict] = {}
        self.results: dict[str, dict] = {}
        self.started: list[tuple] = []
        self.clarified: list[tuple] = []
        self.cancelled: list[str] = []
        self.reject_clarify = False

    def _check(self) -> None:
        if not self.connected:
            raise Unavailable("temporal is not reachable yet")

    async def start_ingest(self, corpus_version, continue_as_new_events):
        self._check()
        self.started.append(("ingest", corpus_version))
        return f"ingest-{corpus_version}"

    async def start_query(self, query_id, question, clarify_timeout_seconds):
        self._check()
        self.started.append(("query", query_id, question))
        self.statuses[f"research-{query_id}"] = "RUNNING"

    async def status(self, workflow_id):
        self._check()
        if workflow_id not in self.statuses:
            raise NotFound(workflow_id)
        return self.statuses[workflow_id]

    async def query(self, workflow_id, name):
        return self.queries[workflow_id]

    async def result(self, workflow_id):
        return self.results[workflow_id]

    async def clarify(self, workflow_id, choice):
        if self.reject_clarify:
            raise Conflict("query is running, not awaiting input")
        self.clarified.append((workflow_id, choice))

    async def cancel(self, workflow_id):
        self.cancelled.append(workflow_id)

    async def healthy(self):
        return self.connected


def event_fields(step: int, type_: str, content: str = "x") -> dict:
    return {"ts": "2026-10-26T00:00:00+00:00", "step": str(step), "node": "plan", "type": type_,
            "content": content, "meta": json.dumps({})}
