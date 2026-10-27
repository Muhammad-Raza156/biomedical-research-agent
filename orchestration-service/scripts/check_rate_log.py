"""Evidence: prove the NCBI rate limit held across worker replicas.

Usage (from orchestration-service/, stack up):
   python scripts/check_rate_log.py [redis_url] [limit_per_second]
"""

import sys
from collections import Counter

import redis

url = sys.argv[1] if len(sys.argv) > 1 else "redis://localhost:16379/0"
limit = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0

r = redis.Redis.from_url(url, decode_responses=True)
entries = r.xrange("ncbi:request_log")
times = sorted((int(f["ts_ms"]), f["worker"]) for _, f in entries)
print(f"requests logged: {len(times)}")
print("per worker:", dict(Counter(w for _, w in times)))

worst, worst_at = 0, None
j=0
for i, (t,_) in enumerate(times): # sliding 1000 ms window
    while times[j][0] <= t - 1000:
        j += 1
    if i-j+1 > worst:
        worst, worst_at = i-j+1, t
print(f"max requests in any 1-second window: {worst} (limit {limit}) at ts_ms={worst_at}")
print("RESULT:", "PASS" if worst <= limit else "FAIL")