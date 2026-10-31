"""Kill a container when the N-th event of a type appears (N defaults to 1). Usage:
    python scripts/chaos_kill.py ingest:ingest-v1-chaos batch_fetched orchestration-worker-1
    python scripts/chaos_kill.py thinking:<query_id> node_exit orchestration-worker-1 2
"""

import subprocess
import sys
from datetime import UTC, datetime

import redis

stream, event_type, container = sys.argv[1], sys.argv[2], sys.argv[3]
wanted = int(sys.argv[4]) if len(sys.argv) > 4 else 1
seen = 0
r = redis.Redis.from_url("redis://localhost:16379/0", decode_responses=True)
last_id = "$"                               # only events that arrive from now on
while True:
    # block below redis-py's default 5 s socket timeout; `or []` turns an empty wait into "ask again"
    for _, entries in r.xread({stream: last_id}, block=2_000) or []:
        for entry_id, fields in entries:
            last_id = entry_id
            if fields.get("type") == event_type:
                seen += 1
                if seen < wanted:
                    continue
                print(datetime.now(UTC).isoformat(), "saw", event_type, entry_id, fields.get("content"))
                # check=False: we report Docker's return code ourselves instead of raising
                result = subprocess.run(["docker", "kill", container], capture_output=True, text=True, check=False)
                print(datetime.now(UTC).isoformat(), "kill", container, "->", (result.stdout or result.stderr).strip())
                sys.exit(result.returncode)
