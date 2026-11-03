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

**What we did:** `python scripts/concurrent_queries.py http://localhost:8000 20`. At 07:06:57Z this sent 20
`POST /queries` at once (four Tier-1 questions in rotation), and each client subscribed to its own SSE stream.

**What happened** (2026-11-01, model `gpt-oss-20b`):
- **All 20 were accepted** (202), and all 20 `ResearchWorkflow`s started within 1 s.
- **Docker Desktop crashed and restarted itself** about 6 minutes in.
  - Every container restarted, and the test client's SSE connections were cut ("peer closed connection").
  - The cause wasn't determined: the host still had 14 GB of RAM free.
  - After the restart Temporal still had all 20 workflows, and they carried on. One stream shows the `checkpoint`
    resume event.
- **The provider's daily cap was hit:** by then the free-tier allowance for `gpt-oss-20b` (200k tokens/day) was used
  up by that morning's evaluation. Every agent run got HTTP 429 "tokens per day" at its first LLM call. After 5
  attempts each workflow published a terminal `error` and ended *Failed*.
- **Checked from the 20 Redis streams afterwards:**
  - steps monotonic and unique in **20/20**;
  - every stream contains only its own query's events and ends with exactly one terminal event;
  - the last one ended at 07:23:09Z.

**Result (2026-11-01):** PARTIAL. Acceptance, durability and clean streams were proven, but no answers came back,
because the provider's daily cap had been reached.

**Re-run, 2026-11-03** (clean-checkout stack, `gpt-oss-120b`, **two worker replicas**):
```
python scripts/concurrent_queries.py http://localhost:8000 20
...
completed: 20/20; steps ok: 20/20; max simultaneous agent runs: 8
```
- All 20 queries were posted at 05:54:08Z; the last answer arrived at 06:09:21Z, i.e. **about 15 minutes** for 20
  agent runs and 153k tokens. The shared `ratelimit:llm` bucket and the provider's 8k tokens/minute set the pace.
- **20/20 streams ended with `answer`.** Steps were monotonic and unique in 20/20, and no run needed a retry.
- **Max simultaneous agent runs: 8** = `RESEARCH_MAX_CONCURRENT_RUNS` (4) × 2 replicas. Concurrency was bounded
  exactly as configured; the rest waited in Temporal's task queue.
- **No cross-talk:** every stream holds only its own question's events and answer.
- **Answer quality, checked by hand: 18/20 correct.**
  - All 15 journal and retraction answers were right, e.g. "PMID 35269852 was published in International journal
    of molecular sciences and is marked as retracted".
  - 3 of the 5 "authors of PMID 33516777" runs named both authors. The other **2 answered "does not contain"**: the
    model wrote `(Paper)-[:AUTHORED]->(Author)`, i.e. the wrong direction, and once used an integer PMID. Neo4j
    returns 0 rows for both without a warning, and one run repeated the identical query 4 times.
  - These are wrong negatives, not fabrications. Agent fix noted in EVAL.md.

**Result (2026-11-03):** PASS for concurrency (20/20 completed, bounded at 8, no cross-talk). Answer quality under
load was 18/20.

**How concurrency is bounded:**
- per worker: `RESEARCH_MAX_CONCURRENT_RUNS=4` agent runs (`max_concurrent_activities`); the rest wait in the task
  queue;
- across workers: every LLM call takes a token from the shared Redis bucket `ratelimit:llm`
  (`LLM_REQUESTS_PER_MINUTE=8`).

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

## 8. Clean-checkout rebuild — the graders' path (2026-11-03)

**What we did:**
```bash
docker compose -f api-service/docker-compose.yml down -v              # wipe every volume
docker compose -f orchestration-service/docker-compose.yml down -v
git clone https://github.com/Muhammad-Raza156/biomedical-research-agent.git bra-clean && cd bra-clean
cp .env.example .env                       # + GROQ_API_KEY, NCBI_API_KEY, NCBI_EMAIL; example passwords kept
docker network create research-net         # "already exists" is fine
docker compose -f orchestration-service/docker-compose.yml up -d --build
docker compose -f api-service/docker-compose.yml up -d --build
```

**What happened:**
- Both stacks were up in 2 min 10 s (05:40:02 → 05:42:12Z). `/health` was 200 with all dependencies up.
- **No other step was needed.**
- The worker started `ingest-v1` by itself (05:42:17Z), and it **completed at 05:44:20Z**: about 2 minutes from
  nothing, with an NCBI API key.
- Every §3 criterion was met: 275 papers (the identical PMID list), 680 CITES, longest chain 13, 135 multi-paper
  authors, 14 MeSH trees, 27 papers with notices. INGESTION_REPORT.md has the numbers and the small drift since
  October.
- **Ingestion kill, on the fresh stack:**
  - `chaos_kill.py ingest:ingest-v2check batch_fetched orchestration-worker-1` killed the worker after the first
    fetch batch (05:44:43Z);
  - `docker start` at 05:45:19Z;
  - Temporal retried 5 activities on attempt 2, and the run completed at 05:46:19Z with **counts identical to
    `ingest-v1`**.
- **Two replicas:** `up -d --scale worker=2` gave `worker-1` and `worker-2`, each polling both task queues.
- **Read-only user:** `CREATE`, `SET` and `DETACH DELETE` as `agent_reader` were all refused (`Forbidden`).
- **Tests on the rebuilt stack:**
  - unit: 144 worker + 19 API;
  - integration: 14 + 2, including end-to-end idempotency.

**Result:** PASS. A fresh clone with only the README steps reproduces the system and its corpus.

## Also observed

- **The API starts without Temporal.** With Temporal stopped, `/health` → 503 `temporal: down` and
  `POST /queries` → 503. After `docker compose start temporal`, `/health` → 200. The API container restarted 0 times.
  See `docs/evidence/m4-api-end-to-end.txt`.
- **A provider error mid-query.** Groq returned a 400 `json_validate_failed` inside `synthesize`. Temporal retried
  the activity, and it resumed from the checkpoint at `synthesize`. These errors are now retried inside the LLM client.
- **Provider daily cap.** Groq's 429 "tokens per day" fails the activity. After 5 attempts the workflow publishes a
  terminal `error` (reason `failed`) and ends *Failed*, so the client sees a clean end of stream instead of a hang.
