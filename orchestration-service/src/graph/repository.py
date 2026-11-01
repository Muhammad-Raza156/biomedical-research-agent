"""Read-only graph access for the agent (connects as agent_reader). Implements agent.ports.GraphReader."""

from __future__ import annotations

from neo4j import READ_ACCESS, AsyncDriver, unit_of_work
from neo4j.exceptions import (
    DriverError,
    Neo4jError,
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)

from agent.ports import ExplainInfo, GraphUnavailable, QueryFailed
from agent.schemas import Assertion
from ingestion.authors import normalise_name

VERIFY_TEMPLATES: dict[str, str] = {
    "authored": "MATCH (a:Author)-[:AUTHORED]->(p:Paper {pmid: $pmid}) "
                "WHERE a.author_id = $author OR toLower(a.display_name) = toLower($author) RETURN count(*) > 0 AS ok",
    "mentions": "MATCH (p:Paper {pmid: $pmid})-[:MENTIONS]->(:MeshTerm)-[:BROADER*0..]->(m:MeshTerm) "
                "WHERE m.ui = $mesh OR toLower(m.name) = toLower($mesh) RETURN count(*) > 0 AS ok",
    "published_before": "MATCH (p:Paper {pmid: $pmid}) RETURN p.pub_year < $year AS ok",
    "published_from": "MATCH (p:Paper {pmid: $pmid}) RETURN p.pub_year >= $year AS ok",
    "published_in_year": "MATCH (p:Paper {pmid: $pmid}) RETURN p.pub_year = $year AS ok",
    "cites": "MATCH (:Paper {pmid: $pmid})-[:CITES]->(:Paper {pmid: $target_pmid}) RETURN count(*) > 0 AS ok",
    "in_journal": "MATCH (:Paper {pmid: $pmid})-[:PUBLISHED_IN]->(j:Journal) "
                  "WHERE j.issn = $journal OR toLower(j.title) = toLower($journal) RETURN count(*) > 0 AS ok",
    "retracted": "MATCH (p:Paper {pmid: $pmid}) RETURN p.retracted = true AS ok",
}


def author_key_from_text(text: str) -> str | None:
    """Turn 'Smith J', 'J. Smith' or 'John Smith' into the name key 'smith|j' (None if one word)."""
    tokens = [t for t in normalise_name(text.replace(".", " ")).split() if t]
    if len(tokens) < 2:
        return None
    first, last = tokens[0], tokens[-1]
    if len(last) <= 2 and len(first) > 2:      # "Smith J" / "Menendez JA": surname first
        return f"{first}|{last[0]}"
    return f"{last}|{first[0]}"                 # "J Smith" / "John Smith": surname last


def _collect_operators(plan: dict | None, ops: list[str], rows: list[float]) -> None:
    if not plan:
        return
    ops.append(plan.get("operatorType", ""))
    rows.append(float(plan.get("args", {}).get("EstimatedRows", 0) or 0))
    for child in plan.get("children", []):
        _collect_operators(child, ops, rows)


UNKNOWN_NAME_STATUSES = ("01N50", "01N51", "01N52")  # GQL status: label / relationship type / property key missing


class Neo4jGraphReader:
    """All agent reads run in READ transactions as the read-only user."""

    def __init__(self, driver: AsyncDriver, database: str) -> None:
        self._driver = driver
        self._db = database

    async def _rows(self, cypher: str, timeout: float = 10.0, **params) -> list[dict]:
        @unit_of_work(timeout=timeout)  # server-side transaction timeout (Query objects are session.run-only)
        async def work(tx):
            result = await tx.run(cypher, **params)
            return [record.data() async for record in result]

        try:
            async with self._driver.session(database=self._db, default_access_mode=READ_ACCESS) as session:
                return await session.execute_read(work)
        except (ServiceUnavailable, SessionExpired, TransientError) as exc:
            raise GraphUnavailable(str(exc)) from exc
        except (Neo4jError, DriverError) as exc:  # syntax, timeout, permission, type errors
            raise QueryFailed(str(exc)) from exc

    async def explain(self, cypher: str) -> ExplainInfo:
        try:
            async with self._driver.session(database=self._db, default_access_mode=READ_ACCESS) as session:
                summary = await (await session.run("EXPLAIN " + cypher)).consume()
        except (ServiceUnavailable, SessionExpired, TransientError) as exc:
            raise GraphUnavailable(str(exc)) from exc
        except (Neo4jError, DriverError) as exc:
            raise QueryFailed(str(exc)) from exc
        ops, rows = [], []
        _collect_operators(summary.plan, ops, rows)
        # Unknown names are only warnings to Neo4j; a typo like [:BRODER*0..] silently matches zero-length paths.
        unknown = [g.status_description.removeprefix("warn: ") for g in summary.gql_status_objects
                   if g.gql_status in UNKNOWN_NAME_STATUSES]
        return ExplainInfo(operators=ops, max_estimated_rows=max(rows, default=0), unknown_names=unknown)

    async def read(self, cypher: str, timeout: float) -> list[dict]:
        return await self._rows(cypher, timeout)

    async def resolve_author(self, text: str) -> list[dict]:
        rows = await self._rows("""
            MATCH (a:Author)
            WHERE a.name_key = $key OR toLower(a.display_name) = toLower($text)
               OR toLower(a.display_name) CONTAINS toLower($text)
            RETURN a.author_id AS id, a.display_name AS label, COUNT { (a)-[:AUTHORED]->() } AS papers
            ORDER BY papers DESC, label LIMIT 10""", key=author_key_from_text(text) or "", text=text)
        return rows

    async def resolve_mesh(self, text: str) -> list[dict]:
        return await self._rows("""
            MATCH (m:MeshTerm) WHERE m.ui = $text OR toLower(m.name) = toLower($text)
               OR toLower(m.name) CONTAINS toLower($text)
            RETURN m.ui AS id, m.name AS label, COUNT { (:Paper)-[:MENTIONS]->(m) } AS papers
            ORDER BY (toLower(m.name) = toLower($text)) DESC, papers DESC LIMIT 10""", text=text)

    async def resolve_pmid(self, pmid: str) -> dict | None:
        rows = await self._rows("""
            MATCH (p:Paper {pmid: $pmid}) WHERE p.in_corpus
            RETURN p.pmid AS id, p.title AS label, p.retracted AS retracted""", pmid=pmid)
        return rows[0] if rows else None

    async def paper_status(self, pmids: list[str]) -> dict[str, dict]:
        rows = await self._rows("""
            UNWIND $pmids AS pmid MATCH (p:Paper {pmid: pmid})
            RETURN p.pmid AS pmid, coalesce(p.in_corpus, false) AS in_corpus, coalesce(p.retracted, false) AS retracted""",
                                pmids=pmids)
        return {r["pmid"]: {"in_corpus": r["in_corpus"], "retracted": r["retracted"]} for r in rows}

    async def check_assertion(self, assertion: Assertion) -> tuple[bool, str]:
        cypher = VERIFY_TEMPLATES[assertion.kind]
        rows = await self._rows(cypher, **assertion.model_dump(exclude={"kind"}))
        return bool(rows and rows[0]["ok"]), cypher
