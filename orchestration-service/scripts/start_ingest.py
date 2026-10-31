"""Start IngestionWorkflow for a given corpus version label. Usage: python scripts/start_ingest.py v1-chaos"""

import asyncio
import sys

from bootstrap import connect_temporal, ensure_ingestion_started


async def main() -> None:
    client = await connect_temporal("localhost:7233", "default")
    print("run_id:", await ensure_ingestion_started(client, sys.argv[1], 2000))


asyncio.run(main())
