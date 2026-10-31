"""API configuration (read once at start-up)."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiSettings:
    """Only what the API needs; it never sees LLM keys or Neo4j credentials."""

    temporal_address: str
    temporal_namespace: str
    redis_url: str
    graph_http_url: str
    corpus_version: str
    clarify_timeout_seconds: int
    ingest_continue_as_new_events: int
    sse_block_ms: int

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ApiSettings:
        """Build from environment variables (defaults match the docker network)."""
        e = os.environ if env is None else env
        return cls(
            temporal_address=e.get("TEMPORAL_ADDRESS", "temporal:7233"),
            temporal_namespace=e.get("TEMPORAL_NAMESPACE", "default"),
            redis_url=e.get("REDIS_URL", "redis://redis:6379/0"),
            graph_http_url=e.get("GRAPH_HTTP_URL", "http://neo4j:7474"),
            corpus_version=e.get("CORPUS_VERSION", "v1"),
            clarify_timeout_seconds=int(e.get("CLARIFY_TIMEOUT_SECONDS", "600")),
            ingest_continue_as_new_events=int(e.get("INGEST_CONTINUE_AS_NEW_EVENTS", "2000")),
            sse_block_ms=int(e.get("SSE_BLOCK_MS", "15000")),
        )
