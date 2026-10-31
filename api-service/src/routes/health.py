"""GET /health — Temporal, Redis and Neo4j; 503 with per-dependency status if any is down."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from schemas import HealthOut

router = APIRouter(tags=["health"])


async def _redis_ok(redis) -> bool:
    try:
        return bool(await redis.ping())
    except Exception:  # noqa: BLE001 - a health probe reports "down", it never raises
        return False


async def _neo4j_ok(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            return (await client.get(url)).status_code == 200
    except httpx.HTTPError:
        return False


@router.get("/health", response_model=HealthOut, responses={503: {"model": HealthOut}})
async def health(request: Request) -> JSONResponse:
    """Check each dependency independently."""
    checks = {
        "temporal": await request.app.state.gateway.healthy(),
        "redis": await _redis_ok(request.app.state.redis),
        "neo4j": await _neo4j_ok(request.app.state.settings.graph_http_url),
    }
    body = HealthOut(status="ok" if all(checks.values()) else "degraded",
                     dependencies={name: "up" if ok else "down" for name, ok in checks.items()})
    return JSONResponse(body.model_dump(), status_code=200 if all(checks.values()) else 503)
