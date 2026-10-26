"""Worker configuration: every environment variable is read once, here."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


class ConfigError(Exception):
    """Raised at start-up when configuration is missing or invalid."""


_REQUIRED = ("GRAPH_PASSWORD", "GRAPH_READER_PASSWORD", "POSTGRES_PASSWORD", "CORPUS_QUERY")


@dataclass(frozen=True)
class Settings:
    """Typed, immutable view of the worker's configuration."""

    llm_model: str
    llm_temperature: float
    llm_requests_per_minute: float
    llm_price_input_per_mtok: float
    llm_price_output_per_mtok: float
    ollama_base_url: str
    corpus_version: str
    corpus_query: str
    corpus_min_date: str
    corpus_max_date: str
    corpus_max_results: int
    corpus_cutoff_year: int
    ncbi_api_key: str
    ncbi_email: str
    ncbi_tool: str
    ncbi_rate_no_key: float
    ncbi_rate_with_key: float
    fetch_batch_size: int
    link_batch_size: int
    load_batch_size: int
    staging_dir: Path
    ingest_continue_as_new_events: int
    temporal_address: str
    temporal_namespace: str
    redis_url: str
    graph_uri: str
    graph_user: str
    graph_password: str
    graph_database: str
    graph_reader_user: str
    graph_reader_password: str
    postgres_dsn: str
    agent_max_iterations: int
    agent_max_verifier_rejections: int
    agent_max_cypher_retries: int
    cypher_timeout_seconds: float
    cypher_max_limit: int
    clarify_timeout_seconds: int
    research_max_concurrent_runs: int
    stream_maxlen: int
    stream_ttl_seconds: int

    @property
    def ncbi_rate(self) -> float:
        """Allowed NCBI requests per second, shared by all workers."""
        return self.ncbi_rate_with_key if self.ncbi_api_key else self.ncbi_rate_no_key

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build Settings from a mapping (defaults to os.environ)."""
        e = os.environ if env is None else env
        missing = [name for name in _REQUIRED if not e.get(name, "").strip()]
        if missing:
            raise ConfigError(f"missing required environment variables: {', '.join(missing)}")

        def s(name: str, default: str = "") -> str:
            return e.get(name, default).strip()

        def i(name: str, default: int) -> int:
            return int(s(name, str(default)) or default)

        def f(name: str, default: float) -> float:
            return float(s(name, str(default)) or default)

        pg = (f"postgresql://{s('POSTGRES_USER', 'langgraph')}:{quote(s('POSTGRES_PASSWORD'), safe='')}"
              f"@{s('POSTGRES_HOST', 'postgres')}:{s('POSTGRES_PORT', '5432')}"
              f"/{s('POSTGRES_DB', 'langgraph')}")

        return cls(
            llm_model=s("LLM_MODEL", "google_genai:gemini-2.5-flash"),
            llm_temperature=f("LLM_TEMPERATURE", 0.0),
            llm_requests_per_minute=f("LLM_REQUESTS_PER_MINUTE", 8.0),
            llm_price_input_per_mtok=f("LLM_PRICE_INPUT_PER_MTOK", 0.0),
            llm_price_output_per_mtok=f("LLM_PRICE_OUTPUT_PER_MTOK", 0.0),
            ollama_base_url=s("OLLAMA_BASE_URL", "http://host.docker.internal:11434"),
            corpus_version=s("CORPUS_VERSION", "v1"),
            corpus_query=s("CORPUS_QUERY"),
            corpus_min_date=s("CORPUS_MIN_DATE", "2005"),
            corpus_max_date=s("CORPUS_MAX_DATE", "2025"),
            corpus_max_results=i("CORPUS_MAX_RESULTS", 400),
            corpus_cutoff_year=i("CORPUS_CUTOFF_YEAR", 2016),
            ncbi_api_key=s("NCBI_API_KEY"),
            ncbi_email=s("NCBI_EMAIL"),
            ncbi_tool=s("NCBI_TOOL", "biomed-research-agent"),
            ncbi_rate_no_key=f("NCBI_RATE_NO_KEY", 2.5),
            ncbi_rate_with_key=f("NCBI_RATE_WITH_KEY", 9.0),
            fetch_batch_size=i("FETCH_BATCH_SIZE", 50),
            link_batch_size=i("LINK_BATCH_SIZE", 50),
            load_batch_size=i("LOAD_BATCH_SIZE", 50),
            staging_dir=Path(s("STAGING_DIR", "/data/staging")),
            ingest_continue_as_new_events=i("INGEST_CONTINUE_AS_NEW_EVENTS", 2000),
            temporal_address=s("TEMPORAL_ADDRESS", "temporal:7233"),
            temporal_namespace=s("TEMPORAL_NAMESPACE", "default"),
            redis_url=s("REDIS_URL", "redis://redis:6379/0"),
            graph_uri=s("GRAPH_URI", "bolt://neo4j:7687"),
            graph_user=s("GRAPH_USER", "neo4j"),
            graph_password=s("GRAPH_PASSWORD"),
            graph_database=s("GRAPH_DATABASE", "neo4j"),
            graph_reader_user=s("GRAPH_READER_USER", "agent_reader"),
            graph_reader_password=s("GRAPH_READER_PASSWORD"),
            postgres_dsn=pg,
            agent_max_iterations=i("AGENT_MAX_ITERATIONS", 12),
            agent_max_verifier_rejections=i("AGENT_MAX_VERIFIER_REJECTIONS", 2),
            agent_max_cypher_retries=i("AGENT_MAX_CYPHER_RETRIES", 3),
            cypher_timeout_seconds=f("CYPHER_TIMEOUT_SECONDS", 10.0),
            cypher_max_limit=i("CYPHER_MAX_LIMIT", 200),
            clarify_timeout_seconds=i("CLARIFY_TIMEOUT_SECONDS", 600),
            research_max_concurrent_runs=i("RESEARCH_MAX_CONCURRENT_RUNS", 4),
            stream_maxlen=i("STREAM_MAXLEN", 5000),
            stream_ttl_seconds=i("STREAM_TTL_SECONDS", 604800),
        )
