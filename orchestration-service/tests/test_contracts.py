import json

from shared.contracts import (
    TERMINAL_EVENT_TYPES,
    EventType,
    IngestionInput,
    ResearchInput,
    StreamEvent,
    answer_key,
    ingest_workflow_id,
    meta_json,
    research_workflow_id,
    thinking_stream_key,
)


def test_key_builders_follow_brief_names():
    assert thinking_stream_key("q1") == "thinking:q1"
    assert answer_key("q1") == "answer:q1"
    assert ingest_workflow_id("v1") == "ingest-v1"
    assert research_workflow_id("q1") == "research-q1"


def test_event_round_trips_through_redis_bytes():
    event = StreamEvent(step=3, node="generate_cypher", type=EventType.LLM_CALL,
                        content="asked the LLM for Cypher", meta=meta_json(tokens=120))
    fields = event.to_redis()
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in fields.items())
    as_bytes = {k.encode(): v.encode() for k, v in fields.items()}  # what redis-py returns
    back = StreamEvent.from_redis(as_bytes)
    assert back == event
    assert json.loads(back.meta) == {"tokens": 120}


def test_terminal_detection():
    assert StreamEvent(step=9, type="answer", content="done").is_terminal
    assert StreamEvent(step=9, type="error", content="cancelled").is_terminal
    assert not StreamEvent(step=1, type="node_enter", content="plan").is_terminal
    assert {"answer", "error", "completed"} == set(TERMINAL_EVENT_TYPES)


def test_ts_is_iso8601_utc():
    ts = StreamEvent(step=1, type="reasoning", content="x").ts
    assert ts.endswith("+00:00")


def test_workflow_inputs_have_safe_defaults():
    assert IngestionInput(corpus_version="v1").start_stage == 0
    assert IngestionInput(corpus_version="v1").counts == {}
    assert IngestionInput(corpus_version="v1").continue_as_new_events == 2000
    assert ResearchInput(query_id="q", question="?").clarify_timeout_seconds == 600
