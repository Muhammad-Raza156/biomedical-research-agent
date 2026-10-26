"""Contracts shared by api-service and orchestration-service.

Only names, key builders, enums and message schemas live here — no business logic.
Both services copy this package into their image; neither imports the other's code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

# ── Temporal names ──────────────────────────────────────────────────────────
TASK_QUEUE_INGEST = "ingestion"
TASK_QUEUE_RESEARCH = "research"
INGESTION_WORKFLOW = "IngestionWorkflow"
RESEARCH_WORKFLOW = "ResearchWorkflow"
CLARIFY_UPDATE = "clarify"
STATUS_QUERY = "status"
PROGRESS_QUERY = "progress"
CLARIFY_TIMEOUT_CHOICE = "__timeout__"


def ingest_workflow_id(corpus_version: str) -> str:
    """Workflow ID (and public run_id) of the ingestion for one corpus version."""
    return f"ingest-{corpus_version}"


def research_workflow_id(query_id: str) -> str:
    """Workflow ID of the research run answering one question."""
    return f"research-{query_id}"


# ── Redis keys ──────────────────────────────────────────────────────────────
CORPUS_STATS_KEY = "corpus:stats"
NCBI_BUCKET_KEY = "ratelimit:ncbi"
LLM_BUCKET_KEY = "ratelimit:llm"
NCBI_REQUEST_LOG_KEY = "ncbi:request_log"


def ingest_stream_key(run_id: str) -> str:
    """Redis stream with ingestion progress events (§4.6)."""
    return f"ingest:{run_id}"


def thinking_stream_key(query_id: str) -> str:
    """Redis stream with the agent's thinking steps (§5.8)."""
    return f"thinking:{query_id}"


def answer_key(query_id: str) -> str:
    """Redis string holding the final answer JSON of a query."""
    return f"answer:{query_id}"


def ingest_result_key(run_id: str) -> str:
    """Redis string holding final ingestion counts JSON."""
    return f"ingest:result:{run_id}"


def ingest_seq_key(run_id: str) -> str:
    """Redis counter giving ingestion events a monotonic step number across workers."""
    return f"ingest:seq:{run_id}"


# ── Event types ─────────────────────────────────────────────────────────────
class EventType(StrEnum):
    """Thinking-stream event types, exactly as listed in brief §5.8."""

    NODE_ENTER = "node_enter"
    NODE_EXIT = "node_exit"
    GRAPH_QUERY = "graph_query"
    GRAPH_RESULT = "graph_result"
    LLM_CALL = "llm_call"
    REASONING = "reasoning"
    CLARIFICATION_NEEDED = "clarification_needed"
    VERIFICATION = "verification"
    CHECKPOINT = "checkpoint"
    ANSWER = "answer"
    ERROR = "error"


class IngestEventType(StrEnum):
    """Ingestion-stream event types (brief §4.6)."""

    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    BATCH_FETCHED = "batch_fetched"
    BATCH_LINKED = "batch_linked"
    BATCH_PARSED = "batch_parsed"
    PAPERS_WRITTEN = "papers_written"
    RECORD_SKIPPED = "record_skipped"
    RETRY = "retry"
    COMPLETED = "completed"
    ERROR = "error"


TERMINAL_EVENT_TYPES = frozenset({EventType.ANSWER.value, EventType.ERROR.value,
                                  IngestEventType.COMPLETED.value})


class QueryStatus(StrEnum):
    """Public status of a research query (brief §7)."""

    RUNNING = "running"
    AWAITING_INPUT = "awaiting_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _text(value: bytes | str) -> str:
    return value.decode() if isinstance(value, bytes) else value


class StreamEvent(BaseModel):
    """One entry in a Redis stream (thinking or ingest). Schema from brief §5.8."""

    ts: str = Field(default_factory=_now_iso)
    step: int
    node: str = ""
    type: str
    content: str
    meta: str = ""

    def to_redis(self) -> dict[str, str]:
        """Flatten to the str→str mapping that XADD expects."""
        return {name: str(value) for name, value in self.model_dump().items()}

    @classmethod
    def from_redis(cls, fields: dict[bytes, bytes] | dict[str, str]) -> StreamEvent:
        """Rebuild an event from XREAD/XRANGE output (bytes or str)."""
        data = {_text(k): _text(v) for k, v in fields.items()}
        return cls(**data)

    @property
    def is_terminal(self) -> bool:
        """True for the last event of a stream (answer, error, completed)."""
        return self.type in TERMINAL_EVENT_TYPES


def meta_json(**fields: object) -> str:
    """Build the `meta` JSON string; non-JSON values (e.g. dates) become strings."""
    return json.dumps(fields, default=str)


# ── Workflow inputs (dataclasses: Temporal's default converter handles them) ─
@dataclass
class IngestionInput:
    """Input of IngestionWorkflow. start_stage/counts carry state across continue_as_new.

    continue_as_new_events is passed in (not read from env) because workflow code must not do I/O.
    """

    corpus_version: str
    start_stage: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    continue_as_new_events: int = 2000


@dataclass
class ResearchInput:
    """Input of ResearchWorkflow."""

    query_id: str
    question: str
    clarify_timeout_seconds: int = 600
