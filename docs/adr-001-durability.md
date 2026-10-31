# ADR-001: Temporal ↔ LangGraph durability boundary

Status: Accepted · Date: 2026-10-31

## Context
Temporal and LangGraph can both checkpoint and retry. The agent is a loop of 8 nodes
(plan → generate_cypher → validate_cypher → execute → observe → synthesize → verify, plus clarify) that
may pause with `interrupt()` to ask the user which author or MeSH term they meant. We need:
- resume after a worker crash without restarting from `plan`;
- thinking steps that are never duplicated;
- a clarification wait that survives restarts;
- cancellation;
- no LLM or Neo4j I/O in workflow code.

## Decision
**Option A:** the whole LangGraph run executes inside one heartbeating Temporal activity, `run_agent`.
- **LangGraph** owns the reasoning loop, the routing and `interrupt()`. Its `AsyncPostgresSaver` checkpoints the state after every node (`thread_id = query_id`).
- **`ResearchWorkflow`** (Temporal) owns the job:
  - retries: 5 attempts, back-off 5 s → 60 s;
  - a 30 s heartbeat timeout to detect a dead worker (the activity heartbeats every 10 s);
  - the durable 10-minute clarification timer;
  - the `clarify` update, whose validator rejects calls when the query isn't waiting;
  - cancellation and visibility.

## Why not B (one activity per node)
B would re-implement LangGraph's conditional routing and `interrupt()` in workflow code: two
orchestrators for one loop, which the brief marks down under KISS. A gives each concern one owner and needs
one activity type for the agent, plus a tiny `publish_terminal` activity.

## Consequences
- **Crash → resume at the next unfinished node.** The new attempt reopens the thread (`aget_state`) and continues
  with `ainvoke(None)`; completed nodes don't run again.
  - Evidence `docs/evidence/m4-kill-resume.txt`: worker killed after `generate_cypher`, attempt 2
    continued at `validate_cypher`, `plan` entered once.
  - Evidence `m4-neo4j-restart.txt`: Neo4j killed mid-query; two attempts failed with `GraphUnavailable`
    and attempt 3 resumed once Neo4j was back (~33 s). The 5-attempt policy covers that.
- **Paying twice.** An LLM call in flight when the worker dies is lost. It is re-issued once, when its node
  re-runs; completed nodes' calls are never repeated, because their results are in the checkpoint.
  - The provider's schema-validation 400s (`json_validate_failed`, `output_parse_failed`) are retried
    inside the LLM client, so they don't cost a whole activity attempt.
  - Avoiding even the one repeat would need a response cache keyed by checkpoint id (future work).
- **No duplicated steps.** Step numbers live in the checkpointed state. On resume, events whose step is ≤ the
  last step already in Redis are suppressed, and one `checkpoint` event marks where the run continued.
  The stream stays monotonic with no duplicates (`check_stream.py`: PASS in both runs).
- **Answer exactly once.** The answer is written to `answer:{query_id}` before the `answer` event. A retry
  that finds a finished thread, or finds `answer` already last in the stream, publishes nothing new.
- **Each failure type is handled at its own level:**
  - LLM errors retry inside the client (provider back-off, shared Redis rate limit);
  - invalid Cypher is fed back to `generate_cypher`;
  - Neo4j unavailability escapes the graph and Temporal retries it;
  - after the last attempt, the workflow publishes a terminal `error` (reason `failed`) and ends *Failed*;
  - cancellation publishes `error` (reason `cancelled`), both while waiting and while `run_agent` runs.
- **Trade-off.** Temporal's UI shows one `run_agent` activity (with its attempt count), not one entry per node.
  Per-node detail lives in the thinking stream and the Postgres checkpoints.
