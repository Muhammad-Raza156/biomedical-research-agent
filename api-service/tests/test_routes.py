import asyncio
import json
import threading
import time

from shared.contracts import answer_key, thinking_stream_key

from tests.fakes import event_fields

ANSWER = {"status": "completed", "answer": "- claim (PMID 1)", "claims": [
    {"text": "claim", "pmids": ["1"], "cypher": "MATCH …", "verification_queries": [], "warnings": []}],
    "claims_checked": 1, "claims_passed": 1, "claims_dropped": 0, "total_tokens": 10}


def add(redis, key, *events):
    for e in events:
        asyncio.run(redis.xadd(key, e))


def test_post_query_returns_202_and_starts_workflow(client, fakes):
    r = client.post("/queries", json={"question": "Who wrote PMID 1?"})
    assert r.status_code == 202
    assert fakes[0].started[0][:1] == ("query",) and r.json()["query_id"]


def test_invalid_body_is_422(client):
    assert client.post("/queries", json={}).status_code == 422
    assert client.post("/queries", json={"question": "x"}).status_code == 422


def test_unknown_query_is_404(client):
    assert client.get("/queries/nope").status_code == 404
    assert client.get("/queries/nope/stream").status_code == 404
    assert client.post("/queries/nope/clarify", json={"choice": "a"}).status_code == 404


def test_running_and_awaiting_input_status(client, fakes):
    gateway, _ = fakes
    gateway.statuses["research-q1"] = "RUNNING"
    gateway.queries["research-q1"] = {"status": "awaiting_input",
                                      "clarification": {"mention": "Smith J", "options": [{"id": "a", "label": "A"}]}}
    body = client.get("/queries/q1").json()
    assert body["status"] == "awaiting_input" and body["clarification"]["mention"] == "Smith J"


def test_completed_answer_comes_from_redis(client, fakes):
    gateway, redis = fakes
    gateway.statuses["research-q1"] = "COMPLETED"
    redis.kv[answer_key("q1")] = json.dumps(ANSWER)
    body = client.get("/queries/q1").json()
    assert body["status"] == "completed" and body["answer"]["claims"][0]["pmids"] == ["1"]


def test_cancelled_and_failed(client, fakes):
    gateway, _ = fakes
    gateway.statuses["research-c"] = "CANCELED"
    gateway.statuses["research-f"] = "FAILED"
    assert client.get("/queries/c").json()["status"] == "cancelled"
    assert client.get("/queries/f").json()["status"] == "failed"


def test_clarify_conflict_is_409(client, fakes):
    gateway, _ = fakes
    gateway.statuses["research-q1"] = "RUNNING"
    gateway.reject_clarify = True
    assert client.post("/queries/q1/clarify", json={"choice": "a"}).status_code == 409


def test_cancel_running_202_and_finished_409(client, fakes):
    gateway, _ = fakes
    gateway.statuses["research-q1"] = "RUNNING"
    gateway.statuses["research-q2"] = "COMPLETED"
    assert client.post("/queries/q1/cancel").status_code == 202
    assert client.post("/queries/q2/cancel").status_code == 409
    assert gateway.cancelled == ["research-q1"]


def test_stream_replays_and_closes_after_terminal(client, fakes):
    gateway, redis = fakes
    gateway.statuses["research-q1"] = "RUNNING"
    key = thinking_stream_key("q1")
    add(redis, key, event_fields(1, "node_enter"), event_fields(2, "llm_call"), event_fields(3, "answer"))
    text = client.get("/queries/q1/stream").text
    assert [line for line in text.split("\n") if line.startswith("id:")] == ["id: 1001-0", "id: 1002-0", "id: 1003-0"]
    assert "event: answer" in text


def test_stream_resumes_after_last_event_id(client, fakes):
    gateway, redis = fakes
    gateway.statuses["research-q1"] = "RUNNING"
    key = thinking_stream_key("q1")
    add(redis, key, event_fields(1, "node_enter"), event_fields(2, "llm_call"), event_fields(3, "answer"))
    text = client.get("/queries/q1/stream", headers={"Last-Event-ID": "1001-0"}).text
    assert [line for line in text.split("\n") if line.startswith("id:")] == ["id: 1002-0", "id: 1003-0"]
    done = client.get("/queries/q1/stream", headers={"Last-Event-ID": "1003-0"}).text
    assert "id:" not in done                                   # already finished → closes at once


def test_subscribing_before_the_first_event_waits_for_it(client, fakes):
    gateway, redis = fakes
    gateway.statuses["research-q1"] = "RUNNING"                # workflow started, stream key not created yet

    def worker_publishes_later():
        time.sleep(0.2)
        add(redis, thinking_stream_key("q1"), event_fields(1, "node_enter"), event_fields(2, "answer"))

    threading.Thread(target=worker_publishes_later).start()
    text = client.get("/queries/q1/stream").text
    assert [line for line in text.split("\n") if line.startswith("id:")] == ["id: 1001-0", "id: 1002-0"]


def test_malformed_last_event_id_is_422(client, fakes):
    gateway, redis = fakes
    gateway.statuses["research-q1"] = "RUNNING"
    add(redis, thinking_stream_key("q1"), event_fields(1, "answer"))
    assert client.get("/queries/q1/stream", headers={"Last-Event-ID": "abc"}).status_code == 422


def test_expired_stream_of_finished_query_is_404(client, fakes):
    fakes[0].statuses["research-old"] = "COMPLETED"
    assert client.get("/queries/old/stream").status_code == 404


def test_health_reports_each_dependency(client, fakes):
    gateway, redis = fakes
    body = client.get("/health")
    assert body.status_code == 503                             # neo4j probe points at a closed port
    assert body.json()["dependencies"] == {"temporal": "up", "redis": "up", "neo4j": "down"}
    gateway.connected, redis.up = False, False
    assert client.get("/health").json()["dependencies"]["temporal"] == "down"


def test_temporal_not_connected_gives_503(client, fakes):
    fakes[0].connected = False
    assert client.post("/queries", json={"question": "Who wrote PMID 1?"}).status_code == 503


def test_ingest_start_status_and_corpus_stats(client, fakes):
    gateway, redis = fakes
    assert client.post("/ingest").json() == {"run_id": "ingest-v1"}
    gateway.statuses["ingest-v1"] = "COMPLETED"
    redis.kv["ingest:result:ingest-v1"] = json.dumps({"papers": 275, "added": 275, "removed": 0, "unchanged": 0})
    assert client.get("/ingest/ingest-v1").json()["result"]["papers"] == 275
    assert client.get("/corpus/stats").status_code == 503
    redis.kv["corpus:stats"] = json.dumps({"papers": 275})
    assert client.get("/corpus/stats").json() == {"papers": 275}
