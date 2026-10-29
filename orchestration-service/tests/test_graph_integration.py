"""Needs the docker stack (Neo4j Enterprise with the `test` database)."""

import pytest
from neo4j.exceptions import ClientError, Forbidden

from graph.driver import make_driver
from graph.loader import (
    delete_orphan_authors,
    load_authors,
    load_citations,
    load_mesh_hierarchy,
    load_paper_batch,
)
from graph.rows import build_batch_rows
from graph.schema import ensure_schema
from graph.stats import compute_corpus_stats
from ingestion.authors import resolve_authors
from ingestion.citations import CitationEdge
from ingestion.mesh_hierarchy import build_hierarchy
from ingestion.models import AuthorRecord, MeshHeadingRecord, PaperRecord, PubDate

pytestmark = pytest.mark.integration
DB = "test"


# Give each synthetic person an ORCID so the resolver merges their occurrences deterministically.
ORCIDS = {
    "Smith Anna": "0000-0000-0000-0001",
    "Lee Kim": "0000-0000-0000-0002",
    "Park Ji": "0000-0000-0000-0003",
    "Cho Mi": "0000-0000-0000-0004",
}


def p(pmid, year, authors, mesh):
    return PaperRecord(
        pmid=pmid,
        title=f"Paper {pmid}",
        pub_date=PubDate(year=year, precision="year"),
        authors=[
            AuthorRecord(
                position=i + 1,
                last_name=a.split()[0],
                fore_name=a.split()[1],
                orcid=ORCIDS[a],
            )
            for i, a in enumerate(authors)
        ],
        mesh=[MeshHeadingRecord(ui=ui, name=name, major=True) for ui, name in mesh],
    )


PAPERS = [
    p("1", 2010, ["Smith Anna", "Lee Kim"], [("D008687", "Metformin")]),
    p("2", 2014, ["Lee Kim", "Park Ji"], [("D001943", "Breast Neoplasms")]),
    p("3", 2018, ["Park Ji", "Cho Mi"], [("D015179", "Colorectal Neoplasms")]),
    p("4", 2020, ["Cho Mi"], [("D008687", "Metformin")]),
]
HIERARCHY = build_hierarchy(
    {
        "D008687": ("Metformin", ["D02.078"]),
        "D001943": ("Breast Neoplasms", ["C04.588.180"]),
        "D015179": ("Colorectal Neoplasms", ["C04.588.274"]),
    },
    {
        "D02": ("D009930", "Organic Chemicals"),
        "C04": ("D009369", "Neoplasms"),
        "C04.588": ("D009371", "Neoplasms by Site"),
    },
)
CITES = [
    CitationEdge(src="4", dst="3", source="both"),
    CitationEdge(src="3", dst="2", source="xml"),
    CitationEdge(src="2", dst="1", source="elink"),
]


@pytest.fixture
async def driver(local_env):
    d = make_driver(
        local_env["GRAPH_URI"], local_env["GRAPH_USER"], local_env["GRAPH_PASSWORD"]
    )
    async with d.session(database=DB) as s:
        await s.run("MATCH (n) DETACH DELETE n")
    await ensure_schema(d, DB)
    yield d
    await d.close()


async def load_all(driver):
    resolution = resolve_authors(PAPERS)
    await load_mesh_hierarchy(driver, DB, HIERARCHY)
    await load_authors(driver, DB, resolution.authors)
    await load_paper_batch(
        driver, DB, build_batch_rows(PAPERS, resolution.by_paper(), "test")
    )
    await load_citations(driver, DB, CITES)
    await delete_orphan_authors(driver, DB)


async def counts(driver):
    stats = await compute_corpus_stats(driver, DB, cutoff_year=2016)
    return stats["node_counts"], stats["relationship_counts"]


async def rows(driver, cypher, **params):
    async with driver.session(database=DB) as s:
        return [r.data() async for r in await s.run(cypher, **params)]


async def test_loading_twice_gives_identical_counts(driver):
    await load_all(driver)
    first = await counts(driver)
    await load_all(driver)
    assert await counts(driver) == first
    assert first[1]["CITES"] == 3


async def test_temporal_filter(driver):
    await load_all(driver)
    result = await rows(
        driver,
        """
        MATCH (p:Paper)-[:MENTIONS]->(:MeshTerm {name: 'Metformin'})
        WHERE p.in_corpus AND p.pub_year < $cutoff RETURN p.pmid AS pmid""",
        cutoff=2016,
    )
    assert result == [{"pmid": "1"}]


async def test_coauthorship_two_hops(driver):
    await load_all(driver)
    result = await rows(
        driver,
        """
        MATCH (m:MeshTerm {name: 'Colorectal Neoplasms'})<-[:MENTIONS]-(:Paper)<-[:AUTHORED]-(x:Author)
        MATCH (x)-[:AUTHORED]->(:Paper)<-[:AUTHORED]-(co:Author) WHERE co <> x
        RETURN DISTINCT co.display_name AS name ORDER BY name""",
    )
    # Park and Cho wrote on colorectal neoplasms; their co-authors are Lee (with Park), Cho and Park.
    assert [r["name"] for r in result] == ["Cho Mi", "Lee Kim", "Park Ji"]


async def test_mesh_descendants_via_broader(driver):
    await load_all(driver)
    result = await rows(
        driver,
        """
        MATCH (p:Paper)-[:MENTIONS]->(:MeshTerm)-[:BROADER*0..]->(:MeshTerm {name: 'Neoplasms'})
        RETURN DISTINCT p.pmid AS pmid ORDER BY pmid""",
    )
    assert [r["pmid"] for r in result] == ["2", "3"]


async def test_citation_chain_ordered_by_date(driver):
    await load_all(driver)
    result = await rows(
        driver,
        """
        MATCH path = (a:Paper {pmid: '4'})-[:CITES*1..4]->(b:Paper)
        WHERE EXISTS { (b)-[:MENTIONS]->(:MeshTerm {name: 'Metformin'}) }
        RETURN [n IN nodes(path) | n.pmid + ':' + toString(n.pub_year)] AS chain
        ORDER BY length(path) LIMIT 1""",
    )
    assert result[0]["chain"] == ["4:2020", "3:2018", "2:2014", "1:2010"]


async def test_stats_shape(driver):
    await load_all(driver)
    stats = await compute_corpus_stats(driver, DB, cutoff_year=2016)
    assert (
        stats["papers"] == 4
        and stats["before_cutoff"] == 2
        and stats["longest_chain"] == 4
    )
    assert stats["cites_by_source"] == {"both": 1, "xml": 1, "elink": 1}
    assert stats["mesh_top_level_trees"] == ["C", "D"]


async def test_failed_batch_leaves_no_partial_writes(driver):
    async def boom(tx):
        await tx.run("CREATE (:Paper {pmid: 'partial'})")
        raise RuntimeError("simulated crash inside the transaction")

    async with driver.session(database=DB) as s:
        with pytest.raises(RuntimeError):
            await s.execute_write(boom)
    assert await rows(driver, "MATCH (p:Paper {pmid: 'partial'}) RETURN p") == []


async def test_reader_user_cannot_write(local_env):
    reader = make_driver(
        local_env["GRAPH_URI"],
        local_env["GRAPH_READER_USER"],
        local_env["GRAPH_READER_PASSWORD"],
    )
    async with reader.session(database=DB) as s:
        with pytest.raises((Forbidden, ClientError)):
            await (await s.run("CREATE (:Probe)")).consume()
    await reader.close()
