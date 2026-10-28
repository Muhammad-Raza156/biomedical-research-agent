"""Constraints and indexes, created before any data is loaded (idempotent)."""

from neo4j import AsyncDriver

SCHEMA_STATEMENTS = [
    "CREATE CONSTRAINT paper_pmid IF NOT EXISTS FOR (p:Paper) REQUIRE p.pmid IS UNIQUE",
    "CREATE CONSTRAINT author_id IF NOT EXISTS FOR (a:Author) REQUIRE a.author_id IS UNIQUE",
    "CREATE CONSTRAINT journal_issn IF NOT EXISTS FOR (j:Journal) REQUIRE j.issn IS UNIQUE",
    "CREATE CONSTRAINT mesh_ui IF NOT EXISTS FOR (m:MeshTerm) REQUIRE m.ui IS UNIQUE",
    "CREATE CONSTRAINT notice_key IF NOT EXISTS FOR (n:Notice) REQUIRE n.key IS UNIQUE",
    "CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.pub_year)",
    "CREATE INDEX author_name_key IF NOT EXISTS FOR (a:Author) ON (a.name_key)",
    "CREATE INDEX author_display IF NOT EXISTS FOR (a:Author) ON (a.display_name)",
    "CREATE INDEX mesh_name IF NOT EXISTS FOR (m:MeshTerm) ON (m.name)",
]

async def ensure_schema(driver: AsyncDriver, database: str) -> None:
    """Create all constraints/indexes; safe to call on every ingestion run."""
    async with driver.session(database=database) as session:
        for statement in SCHEMA_STATEMENTS:
            await (await session.run(statement)).consume()
        await (await session.run("CALL db.awaitIndexes(60)")).consume()