"""Live acceptance-criteria numbers, computed from the graph."""

from __future__ import annotations

from collections import defaultdict

from neo4j import AsyncDriver

def longest_chain(edges: list[tuple[str, str]]) -> int:
    """Number of papers on the longest simple CITES path (A->B->C->D = 4). Cycle-safen DFS."""
    graph: dict[str, list[str]] = defaultdict(list)
    for src, dst in edges:
        graph[src].append(dst)
    best: dict[str, int] = {}

    def visit(node: str, on_path: set[str]) -> int:
        if node in best:
            return best[node]
        on_path.add(node)
        length = 1 + max((visit(n, on_path) for n in graph[node] if n not in on_path), default=0)
        on_path.discard(node)
        best[node] = length
        return length
    return max((visit(node, set()) for node in list(graph)), default=0)

async def compute_corpus_stats(driver: AsyncDriver, database: str, cutoff_year: int) -> dict:
    """Run the acceptance-criteria queries and return one JSON-serialisable dict."""
    async with driver.session(database=database) as s:
        papers = await (await s.run("""
            MATCH (p:Paper {in_corpus: true})
            RETURN count(p) AS papers, min(p.pub_year) AS min_year, max(p.pub_year) AS max_year,
                   sum(CASE WHEN p.pub_year < $cutoff THEN 1 ELSE 0 END) AS befor_cutoff,
                   sum(CASE WHEN p.pub_year >= $cutoff THEN 1 ELSE 0 END) AS after_cutoff,
                   sum(CASE WHEN p.retracted THEN 1 ELSE 0 END) AS retracted_papers
            """, cutoff=cutoff_year)).single()
        by_source = {r["source"]:r["n"] async for r in await s.run(
            "MATCH (:Paper)-[r:CITES]->(:Paper) RETURN r.source AS source, count(*) AS n"  
        )}
        edges = [(r["a"], r["b"]) async for r in await s.run(
            "MATCH (a:Paper)-[:CITES]->(b:Paper) RETURN a.pmid AS a, b.pmid AS b")]
        authors = await (await s.run("""
            MATCH (a:Author) WHERE NOT coalesce(a.collective, false)
            WITH a, COUNT { (a)-[:AUTHORED]->(:Paper) } AS n
            WHERE n >= 2 RETURN count(a) AS n
            """)).single()
        trees = await(await s.run("""
            MATCH (:Paper {in_corpus: true})-[:MENTIONS]->(m:MeshTerm)
            UNWIND m.categories AS letter
            RETURN collect(DISTINCT letter) AS letters
            """)).single()
        notices = await (await s.run("""
            MATCH (p:Paper {in_corpus: true})
            WHERE EXISTS { (p)-[:RETRACTED_BY|CORRECTED_BY|COMMENTED_IN]->() }
            RETURN count(p) AS n
            """)).single()
        nodes = {r["label"]: r["n"] async for r in await s.run(
            "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n")}
        rels = {r["type"]: r["n"] async for r in await s.run(
            "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS n")}
    return {
        "papers": papers["papers"], "min_year": papers["min_year"], "max_year":papers["max_year"],
        "date_span_years": (papers["max_year"] or 0) - (papers["min_year"] or 0),
        "cutoff_year": cutoff_year, "before_cutoff": papers["before_cutoff"],
        "after_cutoff": papers["after_cutoff"], "retracted_papers": papers["retracted_papers"],
        "cites_total": len(edges), "cites_by_sources": by_source, 
        "longest_chain": longest_chain(edges), 
        "authors_multi_paper": authors["n"], "mesh_top_level_trees": sorted(trees["letters"]),
        "papers_with_notices": notices["n"], "node_counts": nodes,
        "relationship_counts": rels
    }
