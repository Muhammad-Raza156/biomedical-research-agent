"""POST /ingest, GET /ingest/{run_id}, GET /ingest/{run_id}/stream."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from shared.contracts import PROGRESS_QUERY, ingest_result_key, ingest_stream_key

from schemas import IngestStatusOut, RunIdOut
from sse import require_stream, sse_response

router = APIRouter(tags=["ingestion"])


@router.post("/ingest", status_code=202, response_model=RunIdOut)
async def start_ingest(request: Request) -> RunIdOut:
    """Start (or reuse) the ingestion run for the configured corpus version."""
    s = request.app.state.settings
    run_id = await request.app.state.gateway.start_ingest(s.corpus_version, s.ingest_continue_as_new_events)
    return RunIdOut(run_id=run_id)


@router.get("/ingest/{run_id}", response_model=IngestStatusOut)
async def ingest_status(run_id: str, request: Request) -> IngestStatusOut:
    """Progress while running; final counts (incl. added/removed/unchanged) when finished."""
    gateway, redis = request.app.state.gateway, request.app.state.redis
    status = await gateway.status(run_id)
    if status == "RUNNING":
        progress = await gateway.query(run_id, PROGRESS_QUERY)
        return IngestStatusOut(run_id=run_id, status="running", stage=progress["stage"], progress=progress["counts"])
    stored = await redis.get(ingest_result_key(run_id))
    return IngestStatusOut(run_id=run_id, status=status.lower(), result=json.loads(stored) if stored else None)


@router.get("/ingest/{run_id}/stream")
async def ingest_stream(run_id: str, request: Request):
    """SSE of ingest:{run_id} with replay and Last-Event-ID."""
    await require_stream(request, ingest_stream_key(run_id), run_id)
    return sse_response(request, ingest_stream_key(run_id))
