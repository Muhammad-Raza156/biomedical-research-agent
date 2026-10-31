# Resilience evidence (brief §9.3)

Every scenario below was run on the full docker stack on 2026-10-30/31 (UTC). Raw outputs are in
`docs/evidence/`. Commands are run from `orchestration-service/` with `PYTHONPATH=src;..` unless noted.

`docker kill` counts as a manual stop for `restart: unless-stopped`, so after a kill we restart the container
ourselves with `docker start <name>`.

---

## 1. Worker killed mid-fetch and mid-load (ingestion)

**What we did:**
1. Started a separate run of the same corpus: `python scripts/start_ingest.py v1-chaos` (workflow `ingest-v1-chaos`).
2. `python scripts/chaos_kill.py ingest:ingest-v1-chaos batch_fetched orchestration-worker-1` killed the worker at
   05:06:03Z, while fetch activities were running.
3. `docker start orchestration-worker-1`.
4. Killed it again during `parse_load` at 05:28:16Z the same way, then restarted it.

**What happened:**
- After the fetch kill, Temporal showed 27 pending fetch activities. 8 were retried as attempt 2 with
  "Heartbeat timeout"; their last heartbeats were 05:06:02.19–.96.
- The load kill landed on a workflow task (`WorkflowTaskTimedOut`, history event 340). The workflow continued on
  the restarted worker, and no load batch had to be retried.
- There were 15 fetch retries in total: 8 caused by the kill and 7 by NCBI 429/502 answers.
- Evidence:
  - `chaos-v1-describe.txt`, `chaos-v1-history.txt`, `chaos-v1-stream.txt`;
  - `chaos_fetch_pending.png`, `chaos-fetch-history.png` (Temporal UI);
  - `chaos-v1.json` vs `baseline-v1.json` (final counts).

**Result:** PASS. `ingest-v1-chaos` finished with the same counts as the clean run: 275 papers, 1,649 authors,
1,099 MeSH terms, 146 journals, 678 CITES.

## 2. Worker killed mid-query (research)

**What we did:**
1. `python scripts/chaos_kill.py thinking:kill1 node_exit orchestration-worker-1 2` (armed first).
2. `python scripts/start_query.py "Which authors have co-authored with someone who published on breast neoplasms?" kill1`
3. The worker was killed at 15:39:38Z, right after the 2nd completed node (`generate_cypher`).
4. `docker start orchestration-worker-1` at 15:40:22Z, after the 30 s heartbeat timeout.

**What happened:**
- Temporal started `run_agent` **attempt 2** at 15:40:31.
- The stream's first new event is one `checkpoint`: *"resumed from checkpoint after the previous attempt stopped;
  continuing at node 'validate_cypher'"*.
- `plan` and `generate_cypher` were not re-run (`node_enter` count for `plan` = 1).
- `check_stream.py kill1` → `steps monotonic and unique: PASS`. 20 of 20 claims verified.
- Evidence: `docs/evidence/m4-kill-resume.txt`.

**Result:** PASS. The run resumed at the next unfinished node, with no duplicated steps.

## 3. Two worker replicas — the NCBI rate limit holds

**What we did:**
1. `docker compose -p orchestration -f orchestration-service/docker-compose.yml up -d --scale worker=2`
2. `python scripts/start_ingest.py v1-rate`.
3. `python scripts/check_rate_log.py` (defaults: local Redis, 3 req/s) analysed the Redis log of every NCBI request (send time and
   worker id).

**What happened:**
- 40 requests were split 22/18 between the two workers.
- The busiest 1-second window held 3 requests (limit 3.0).
- Evidence: `docs/evidence/rate-v1-two-workers.txt`, `rate-v1.json`.
- The same check over the chaos run (`chaos-v1-ratelog.txt`): 65 requests from 4 worker ids, max 3 per second.

**Result:** PASS. The shared Redis token bucket (one Lua script, using Redis's clock) holds the limit across replicas.
NCBI still answered a few 429s at about 2.5 req/s. The log records when we *send*, not when NCBI receives, and the
fetch activities retry these.

## 4. Neo4j restarted during a query

**What we did:**
1. `python scripts/chaos_kill.py thinking:neo1 graph_query orchestration-neo4j-1`, then
   `python scripts/start_query.py "Which journal published PMID 35269852, and has it been retracted?" neo1`.
2. Neo4j was killed at 15:49:51Z. `docker start orchestration-neo4j-1` followed at once; Neo4j was healthy at 15:50:25Z.

**What happened:**
- `run_agent` attempts 1 (15:50:04) and 2 (15:50:10) failed with `GraphUnavailable`, on the next node's EXPLAIN.
  Temporal waited 5 s, then 10 s.
- Attempt 3 at 15:50:20 resumed at `validate_cypher` and completed at 15:50:33.
- Evidence: `docs/evidence/m4-neo4j-restart.txt`.

**Result:** PASS. The query completed after the restart. The 5-attempt policy gives about 75 s of back-off; this
restart needed about 33 s.

## 5. 20 concurrent queries

**What we did:** `python scripts/concurrent_queries.py http://localhost:8000 20` (four Tier-1 questions in rotation;
each client subscribes to its own SSE stream).

**Result:** PENDING. The run needs about 100k LLM tokens, and the Groq free tier's daily cap (200k tokens/day for
`openai/gpt-oss-120b`) was used up by the evaluation runs on 2026-10-31. It will be filled in once the run is done.
**Concurrency limits:**
- per worker: `RESEARCH_MAX_CONCURRENT_RUNS=4` agent runs (`max_concurrent_activities`);
- across workers: the shared Redis bucket `ratelimit:llm` (`LLM_REQUESTS_PER_MINUTE=8`).

**No cross-talk** by design: each query writes only to `thinking:{query_id}`, and each SSE subscriber reads only
that key.

## 6. Idempotency — ingest the same corpus twice

**What we did:** `pytest -m integration tests/test_idempotency_e2e.py`. It starts `IngestionWorkflow` for corpus `v1`
under a new id (`ingest-v1-idempotency-b6428d`), which reuses the pinned PMID list and the staged XML.

**What happened:**
- Every stage reused staged data: "reused pinned PMID list", "6 batches (6 already staged)", "0 lookup rounds" for MeSH.
- The run took 18 s.
- Before and after, the counts were identical:
  - nodes: Paper 275, Author 1,649, MeshTerm 1,099, Journal 146, Notice 36;
  - relationships: CITES 678 (`both` 674, `xml` 4), AUTHORED 1,877, MENTIONS 3,664, BROADER 1,418;
  - PUBLISHED_IN 275, COMMENTED_IN 30, CORRECTED_BY 3, COMMENTS_ON 2, RETRACTED_BY 1.
- The NCBI request log (`ncbi:request_log`) stayed at 40 entries: zero NCBI requests.
- Re-ingestion also restores data: the `MERGE … SET p += row` load overwrote an abstract we had altered for the
  prompt-injection drill.

**Result:** PASS. Counts are identical and nothing was refetched.

## 7. The agent's database user cannot write

**What we did:** connected as `agent_reader` (the only user the agent uses; role `reader`) and tried three writes.

**What happened:**
```
REFUSED: CREATE (:Probe {x: 1}) -> Forbidden: Creating new node label on database 'neo4j' is not allowed for user 'agent_reader' with roles [PUBLIC, reader]
REFUSED: MATCH (p:Paper {pmid:'19564453'}) SET p.title = 'hacked' -> Forbidden: Set property for property 'title' ... not allowed for user 'agent_reader'
REFUSED: MATCH (p:Paper {pmid:'19564453'}) DETACH DELETE p -> Forbidden: Delete relationship with type 'PUBLISHED_IN' ... not allowed for user 'agent_reader'
papers still 275
```

**Result:** PASS. Writes are refused by the database itself. That is the fourth layer, after the static check,
the EXPLAIN operator check and the read-only transaction (`orchestration-service/src/agent/cypher_safety.py`).

## Also observed

- **The API starts without Temporal.** With Temporal stopped, `/health` → 503 `temporal: down` and
  `POST /queries` → 503. After `docker compose start temporal`, `/health` → 200. The API container restarted 0 times.
  See `docs/evidence/m4-api-end-to-end.txt`.
- **A provider error mid-query.** Groq returned a 400 `json_validate_failed` inside `synthesize`. Temporal retried
  the activity, and it resumed from the checkpoint at `synthesize`. These errors are now retried inside the LLM client.
- **Provider daily cap.** Groq's 429 "tokens per day" fails the activity. After 5 attempts the workflow publishes a
  terminal `error` (reason `failed`) and ends *Failed*, so the client sees a clean end of stream instead of a hang.
