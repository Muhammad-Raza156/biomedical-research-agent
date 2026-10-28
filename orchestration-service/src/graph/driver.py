"""Neo4j driver factory (one driver per process; it pools connections)."""

from neo4j import AsyncDriver, AsyncGraphDatabase


def make_driver(uri: str, user: str, password: str) -> AsyncDriver:
    """Create the async driver. Use bolt:// for a single-instance server."""
    return AsyncGraphDatabase.driver(
        uri, auth=(user, password), max_connection_pool_size=20
    )
