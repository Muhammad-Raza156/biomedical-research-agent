"""Manual smoke test: run from orchestration-service/ with docker stack up"""

import asyncio
import os
from pathlib import Path

import httpx
import redis.asyncio as aioredis

from clients.ncbi import NcbiClient
from clients.rate_limiter import RedisTokenBucket

async def main() -> None:
    redis = aioredis.from_url("redis://localhost:16379/0")
    limiter = RedisTokenBucket(redis, "ratelimit:ncbi", rate_per_second=2.5, log_key="ncbi:request_log")
    async with httpx.AsyncClient() as http:
        client = NcbiClient(http, limiter, tool="smoke", email=os.environ.get("NCBI_EMAIL", "you@example.com"))
        pmids = await client.esearch(
            "metformin[majr] AND neoplasms[majr] AND diabetes mellitus, type 2[mh] "
            "AND (cohort studies[mh] OR meta-analysis[pt])",
            "2005", "2025", 400
        )

        print("count:", len(pmids), "first:", pmids[:5])
        Path("smoke_efetch.xml").write_bytes(await client.efetch_xml(pmids[:3]))
        Path("smoke_elink.xml").write_bytes(await client.elink_refs_xml(pmids[:3]))
    await redis.aclose()

asyncio.run(main())