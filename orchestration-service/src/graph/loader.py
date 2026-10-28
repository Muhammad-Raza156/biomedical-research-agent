"""Transactional, idempotent writes to Neo4j. One function call = one transaction."""

from __future__ import annotations

from neo4j import AsyncDriver, AsyncManagedTransaction

from graph.rows import NOTICE_RELATIONSHIPS, BatchRows
from ingestion.authors import AuthorEntity
from ingestion.citations import CitationEdge
from ingestion.mesh_hierarchy import MeshHierarchy

_OWNED_OUT = "|".join(["PUBLISHED_IN", "MENTIONS", *NOTICE_RELATIONSHIPS.values()])


async def _write(driver: AsyncDriver, database: str, work, *args) -> object:
    async with driver.sessions(database=database) as session:
        return await session.execute_write(work, *args)


# MeSH
async def _mesh_tx(
    tx: AsyncManagedTransaction, terms: list[dict], edges: list[dict]
) -> None:
    await tx.run(
        """
        UNWIND $terms AS t
        MERGE (m:MeshTerm {ui: t.ui})
        SET m.name = t.name, m.tree_numbers = t.tree_numbers, m.category = t.category,
            m.categories = t.categories, m.in_corpus = t.in_corpus, m.is_root
        """,
        terms=terms,
    )
    await tx.run(
        """
        UNWIND $edges AS e
        MATCH (child:MeshTerm {ui: e.child}), (parent:MeshTerm {ui: e.parent})
        MERGE (child)-[:BROADER] ->(parent)
        """,
        edges=edges,
    )


async def load_mesh_hierarchy(
    driver: AsyncDriver, database: str, hierarchy: MeshHierarchy
) -> None:
    """Write all MeshTerm nodes and broader edges in one transaction."""
    terms = [t.model_dump() for t in hierarchy.terms]
    edges = [{"child": c, "parent": p} for c, p in hierarchy.broader]
    await _write(driver, database, _mesh_tx, terms, edges)


# Authors
async def _authors_tx(tx: AsyncManagedTransaction, authors: list[dict]) -> None:
    await tx.run(
        """
        UNWIND $authors AS a
        MERGE (au: Author {author_id: a.authord_id})
        SET au.display_name = a.display_name, au.orcid = a.orcid, au.name_key=a.name_key,
        au.collective = a.collective, au.name_variants = a.name_variants,
        au.merge_evidence = a.merge_evidence
        """,
        authors=authors,
    )


async def load_authors(
    driver: AsyncDriver, database: str, authors: list[AuthorEntity]
) -> None:
    """Upsert every disambiguated author (before paper batches reference them)."""
    rows = [a.model_dump(exclude={"pmids"}) for a in authors]
    await _write(driver, database, _authors_tx, rows)


# Papers (one batch = one transaction)
async def _paper_batch_tx(tx: AsyncManagedTransaction, rows: dict) -> int:
    pmids = [p["pmid"] for p in rows["papers"]]
    result = await tx.run(
        """
        UNWIND $papers AS row
        MERGE (p:Paper {pmid: row.pmid})
        SET p+= row
        RETURN count(p) AS written
        """,
        papers=rows["papers"],
    )
    written = (await result.single())["written"]

    # Remove edges this batch owns, then re-create them: re-run stay correct even if imputs chnaged.
    await tx.run(
        f"""
        UNWIND $pmids AS pmid
        MATCH (p:Paper {{pmid: pmid}})-[r:{_OWNED_OUT}]->()
        DELETE r
        """,
        pmids=pmids,
    )
    await tx.run(
        """
        UNWIND $pmids AS pmid
        MATCH (:Author)-[r:AUTHORED]->(p:Paper {pmid: pmid})
        DELETE r
        """,
        pmids=pmids,
    )

    await tx.run(
        """
        UNWIND $journals AS j
        MATCH (jn:Journal {issn: j.issn})
        SET jn.issn_type = j.issn_type, jn.title = j.title, jn.issn_print = j.issn_print,
            jn.issn_electronic = j.issn_electronic, jn.issn_linking = j.issn_linking
        MERGE (p)-[:PUBLISHED_IN]->(jn)
        """,
        journals=rows["journals"],
    )
    await tx.run(
        """
        UNWIND $authorship AS a
        MATCH (p:Paper {pmid: a.pmid}), (au:Author {author_id: a.author_id})
        MERGE (au)-[r:AUTHORED {position: a.position}]->(p)
        SET r.name_as_written = a.name_as_written, r.affiliations = a.affiliations,
            r.match_rule = a.match_rule
        """,
        authorships=rows["authorships"],
    )
    await tx.run(
        """
        UNWIND $mentions AS m
        MATCH (p:Paper {pmid: m.pmid})
        MERGE (t:MeshTerm {ui: m.ui})
          ON CREATE SET t.name = m.name, t.tree_numbers = [], t.categories = [],
          t.in_corpus = true
        MERGE (p)-[r:MENTIONS]->(t)
        SET r.major = m.major, r.qualifiers = m.qualifiers
        """,
        mentions=rows["mentions"],
    )
    for rel_type, notices in rows["notices"].items():
        # rel_type comes from our own NOTICE_RELATIONSHIPS, never from data,
        # so formatting it into the query is safe (Cypher can't parametrise relationship types).
        await tx.run(
            f"""
           UNWIND $notices AS n
           MATCH (p:Paper {{pmid: n.pmid_from}})
           MERGE (x:Notice {{key: n.key}})
           SET x.pmid = n.pmid, x.type = n.type, x.source = n.source
           MERGE (p)-[:{rel_type}]->(x)
           """,
            notices=notices,
        )
    return written


async def load_paper_batch(driver: AsyncDriver, database: str, rows: BatchRows) -> int:
    """Write one batch of papers and all their edges atomically. Return papers written."""
    return await _write(driver, database, _paper_batch_tx, rows.model_dump())


# Citations and clean-up
async def _citations_tx(tx: AsyncManagedTransaction, edges: list[dict]) -> None:
    await tx.run(
        """
       UNWIND $edges AS e
       MATCH (a:Paper {pmid: e.src}), (b:Paper {pmid: e.dst})
       MERGE (a)-[r:CITES]->(b)
       """,
        edges=edges,
    )


async def load_citations(
    driver: AsyncDriver, database: str, edges: list[CitationEdge]
) -> None:
    """Write all intra-corpus CITES edges (after all papers exits) in one transaction."""
    await _write(driver, database, _citations_tx, [e.model_dump() for e in edges])


async def _orphans_tx(tx: AsyncManagedTransaction) -> int:
    result = await tx.run("""
        MATCH (a:Author) WHERE NOT (a)-[:AUTHORED]->()
        DETACH DELETE a
        RETURN count(*) AS deleted
        """)
    return (await result.single())["deleted"]


async def delete_orphan_authors(driver: AsyncDriver, database: str) -> int:
    """Remove Author nodes no longer attached to any paper (e.g. after a resolver change)."""
    return await _write(driver, database, _orphans_tx)
