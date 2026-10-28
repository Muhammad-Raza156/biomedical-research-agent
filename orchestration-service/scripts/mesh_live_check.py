"""Live check: enrich 3 real descriptors via NLM, then re-run from the cache (run from orchestration-service/)."""

import asyncio
import tempfile
from pathlib import Path

import httpx
import redis.asyncio as aioredis

from clients.mesh import MESH_BUCKET_KEY, MeshClient
from clients.rate_limiter import RedisTokenBucket
from ingestion.mesh_enrichment import enrich_mesh

NAMES = {"D008687": "Metformin", "D009369": "Neoplasms", "D005260": "Female"}
CACHE = Path(tempfile.gettempdir()) / "mesh_cache_live.json"
LOG_KEY = "mesh:request_log"


async def main() -> None:
    CACHE.unlink(missing_ok=True)  # start cold so run 1 really hits NLM
    redis = aioredis.from_url("redis://localhost:16379/0")
    limiter = RedisTokenBucket(redis, MESH_BUCKET_KEY, rate_per_second=5, log_key=LOG_KEY)
    async with httpx.AsyncClient() as http:
        client = MeshClient(http, limiter)
        for run in (1, 2):
            before = await redis.xlen(LOG_KEY)
            hierarchy, stats = await enrich_mesh(NAMES, CACHE, client, on_progress=print)
            print(f"run {run}: {stats}, HTTP requests: {await redis.xlen(LOG_KEY) - before}")
            if run == 1:
                for t in hierarchy.terms:
                    print("  ", t.ui, t.name, t.tree_numbers, t.category, "corpus" if t.in_corpus else "")
    print("cache file:", CACHE)
    await redis.aclose()


asyncio.run(main())