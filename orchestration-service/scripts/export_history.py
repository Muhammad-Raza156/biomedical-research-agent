"""Export a workflow's history for replay tests. Usage:
    python scripts/export_history.py ingest-v1 tests/histories/ingestion_v1.json
"""

import asyncio
import sys
from pathlib import Path

from temporalio.client import Client


async def main(workflow_id: str, out: str) -> None:
    client = await Client.connect("localhost:7233")
    history = await client.get_workflow_handle(workflow_id).fetch_history()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(history.to_json(), encoding="utf-8")
    print(f"saved {len(history.events)} events of {workflow_id} to {out}")


asyncio.run(main(sys.argv[1], sys.argv[2]))
