"""GET /corpus/stats — numbers computed by the worker after ingestion (no Cypher in the API)."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Request
from shared.contracts import CORPUS_STATS_KEY

router = APIRouter(tags=["corpus"])


@router.get("/corpus/stats")
async def corpus_stats(request: Request) -> dict:
    """Live acceptance-criteria numbers (§3) from the latest completed ingestion."""
    stored = await request.app.state.redis.get(CORPUS_STATS_KEY)
    if not stored:
        raise HTTPException(status_code=503, detail="no completed ingestion yet")
    return json.loads(stored)
