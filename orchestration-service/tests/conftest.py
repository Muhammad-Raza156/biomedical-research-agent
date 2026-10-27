"""Shared test fixtures. Integration tests talk to the docker stack via localhost ports."""

import os

import pytest

from config import Settings

LOCAL_ENV = {
    "LLM_MODEL": "fake:fake",
    "CORPUS_VERSION": "test",
    "CORPUS_QUERY": "metformin[majr] AND neoplasms[majr]",
    "TEMPORAL_ADDRESS": "localhost:7233",
    "REDIS_URL": "redis://localhost:16379/15",  # db 15 = test-only Redis database
    "GRAPH_URI": "bolt://localhost:7687",
    "GRAPH_USER": "neo4j",
    "GRAPH_PASSWORD": os.environ.get("GRAPH_PASSWORD", "change-me-neo4j-pw"),
    "GRAPH_DATABASE": "test",
    "GRAPH_READER_USER": "agent_reader",
    "GRAPH_READER_PASSWORD": os.environ.get(
        "GRAPH_READER_PASSWORD", "change-me-reader-pw"
    ),
    "POSTGRES_USER": "langgraph",
    "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "change-me-postgres-pw"),
    "POSTGRES_DB": "langgraph",
    "POSTGRES_HOST": "localhost",
    "POSTGRES_PORT": "15432",
}


@pytest.fixture
def local_env() -> dict[str, str]:
    """Environment for tests running on the Windows host against the docker stack."""
    return dict(LOCAL_ENV)


@pytest.fixture
def settings(local_env, tmp_path) -> Settings:
    """Settings pointing at localhost services, the `test` Neo4j DB and a temp staging dir."""
    return Settings.from_env({**local_env, "STAGING_DIR": str(tmp_path)})
