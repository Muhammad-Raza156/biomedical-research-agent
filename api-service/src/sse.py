"""Redis stream → Server-Sent Events, with replay, Last-Event-ID resume and terminal close."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from shared.contracts import StreamEvent

_ID = re.compile(r"^\d+-\d+$")


def format_sse(entry_id: str, event: StreamEvent) -> str:
    """One SSE message; the id is the Redis entry ID so Last-Event-ID maps straight to XREAD."""
    return f"id: {entry_id}\nevent: {event.type}\ndata: {event.model_dump_json()}\n\n"


def _key(entry_id: str) -> tuple[int, int]:
    ms, seq = entry_id.split("-")
    return int(ms), int(seq)


async def _finished_before(redis, key: str, cursor: str) -> bool:
    """True if the stream's newest event is terminal and the client already has it."""
    newest = await redis.xrevrange(key, count=1)
    if not newest:
        return False
    entry_id, fields = newest[0]
    return StreamEvent.from_redis(fields).is_terminal and _key(entry_id) <= _key(cursor)


async def event_stream(redis, key: str, start_id: str, request: Request, block_ms: int) -> AsyncIterator[str]:
    """Replay from start_id, then follow live; stop after the terminal event or on disconnect."""
    cursor = start_id
    yield "retry: 3000\n\n"
    if await _finished_before(redis, key, cursor):
        return
    while not await request.is_disconnected():
        batch = await redis.xread({key: cursor}, block=block_ms, count=100)
        if not batch:
            if await _finished_before(redis, key, cursor):
                return
            yield ": keep-alive\n\n"
            continue
        for _stream, entries in batch:
            for entry_id, fields in entries:
                cursor = entry_id
                event = StreamEvent.from_redis(fields)
                yield format_sse(entry_id, event)
                if event.is_terminal:
                    return


def parse_last_event_id(request: Request) -> str:
    """Last-Event-ID header (or ?last_event_id=) → XREAD cursor; '0-0' replays everything."""
    value = request.headers.get("last-event-id") or request.query_params.get("last_event_id") or "0-0"
    if not _ID.match(value):
        raise HTTPException(status_code=422, detail="Last-Event-ID must look like '<ms>-<seq>'")
    return value


async def require_stream(request: Request, key: str, workflow_id: str) -> None:
    """404 for unknown IDs and for finished runs whose stream has expired (retention policy)."""
    if await request.app.state.redis.exists(key):
        return
    status = await request.app.state.gateway.status(workflow_id)   # NotFound → 404
    if status != "RUNNING":
        raise HTTPException(status_code=404,
                            detail="stream expired (retention: ~5000 events, 7 days after the end); "
                                   "the final result is still available from the status endpoint")


def sse_response(request: Request, key: str) -> StreamingResponse:
    """Build the streaming response for one subscriber."""
    start = parse_last_event_id(request)
    settings = request.app.state.settings
    return StreamingResponse(
        event_stream(request.app.state.redis, key, start, request, settings.sse_block_ms),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
