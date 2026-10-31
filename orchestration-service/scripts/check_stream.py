"""Print a thinking stream and verify step numbers. Usage: python scripts/check_stream.py <query_id>"""

import sys
from collections import Counter

import redis

r = redis.Redis.from_url("redis://localhost:16379/0", decode_responses=True)
entries = r.xrange(f"thinking:{sys.argv[1]}")
steps = [int(f["step"]) for _, f in entries]
for entry_id, f in entries:
    print(f"{entry_id}  step={f['step']:>3}  {f['node']:<16} {f['type']:<20} {f['content'][:90]}")
enters = Counter(f["node"] for _, f in entries if f["type"] == "node_enter")
print("\nnode_enter counts:", dict(enters))
print("checkpoint events:", sum(f["type"] == "checkpoint" for _, f in entries))
ok = steps == sorted(steps) and len(steps) == len(set(steps))
print("steps monotonic and unique:", "PASS" if ok else "FAIL")
