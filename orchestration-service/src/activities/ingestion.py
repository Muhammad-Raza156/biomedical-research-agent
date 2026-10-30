"""Ingestion activities: all real work (HTTP, files, Neo4j, Redis) for the five stages."""

from __future__ import annotations

import json
import math
from typing import Protocol

from lxml import etree
from neo4j import AsyncDriver
from shared.contracts import (
    CORPUS_STATS_KEY,
    IngestEventType,
    ingest_result_key,
    meta_json,
)
from temporalio import activity

from activities.types import BatchInput, EventInput, StageResult
from clients.mesh import MeshLookup
from clients.ncbi import NcbiClient, NcbiRetryableError
from config import Settings
from graph import loader
from graph.rows import build_batch_rows
from graph.schema import ensure_schema
from graph.stats import compute_corpus_stats
from ingestion.authors import AuthorResolution, resolve_authors
from ingestion.citations import parse_elink_refs, reconcile_citations
from ingestion.mesh_enrichment import enrich_mesh
from ingestion.mesh_hierarchy import MeshHierarchy
from ingestion.models import PaperRecord, SkippedRecord
from ingestion.parser import parse_pubmed_xml
from ingestion.staging import (
    StagingArea,
    atomic_write_bytes,
    atomic_write_text,
    read_json,
)


class CorpusConfigMismatch(Exception):
    """Corpus settings changed but CORPUS_VERSION did not (permanent: bump the version)."""


class EventSink(Protocol):
    """The part of EventPublisher the activities use (a fake in unit tests)."""

    async def publish_ingest(
        self, run_id: str, type: str, content: str, meta: str = ""
    ) -> str: ...

    async def set_json(
        self, key: str, value: object, ttl_seconds: int | None = None
    ) -> None: ...


class IngestionActivities:
    """Activities share clients injected once per worker process."""

    def __init__(
        self,
        settings: Settings,
        ncbi: NcbiClient,
        mesh: MeshLookup | None,
        driver: AsyncDriver | None,
        events: EventSink,
    ) -> None:
        self._s = settings
        self._ncbi = ncbi
        self._mesh = mesh
        self._driver = driver
        self._events = events

    def all(self) -> list:
        """Bound activity methods to register with the Temporal Worker."""
        return [
            self.discover,
            self.fetch_batch,
            self.link_all,
            self.enrich,
            self.prepare_load,
            self.load_reference_data,
            self.load_batch,
            self.finalize,
            self.publish_event,
        ]

    # ── helpers ───────────────────────────────────────────────────────────
    def _area(self, version: str) -> StagingArea:
        return StagingArea(self._s.staging_dir, version)

    def _config(self) -> dict:
        s = self._s
        return {
            "query": s.corpus_query,
            "mindate": s.corpus_min_date,
            "maxdate": s.corpus_max_date,
            "max_results": s.corpus_max_results,
            "cutoff_year": s.corpus_cutoff_year,
            "fetch_batch_size": s.fetch_batch_size,
            "link_batch_size": s.link_batch_size,
        }

    async def _emit(self, type: str, content: str, **meta: object) -> None:
        await self._events.publish_ingest(
            activity.info().workflow_id, type, content, meta_json(**meta)
        )

    async def _note_retry(self) -> None:
        info = activity.info()
        if info.attempt > 1:
            await self._emit(
                IngestEventType.RETRY,
                f"{info.activity_type} retry, attempt {info.attempt}",
                activity=info.activity_type,
                attempt=info.attempt,
            )

    def _pmids(self, version: str) -> list[str]:
        return read_json(self._area(version).pmids_file)

    def _xml_batches(self, version: str) -> int:
        return math.ceil(len(self._pmids(version)) / self._s.fetch_batch_size)

    def _parsed_papers(self, version: str) -> list[PaperRecord]:
        area, papers, index = self._area(version), [], 0
        while area.parsed_batch(index).exists():
            papers += [
                PaperRecord.model_validate(p)
                for p in read_json(area.parsed_batch(index))["papers"]
            ]
            index += 1
        return papers

    # ── stage 1: discover ─────────────────────────────────────────────────
    @activity.defn
    async def discover(self, corpus_version: str) -> StageResult:
        """Run esearch once per corpus version and pin the PMID list in staging."""
        await self._note_retry()
        area, config = self._area(corpus_version), self._config()
        if area.pmids_file.exists():
            if read_json(area.corpus_config_file) != config:
                raise CorpusConfigMismatch(
                    f"corpus settings differ from the pinned {corpus_version}; set a new CORPUS_VERSION"
                )
            pmids, detail = read_json(area.pmids_file), "reused pinned PMID list"
        else:
            pmids = await self._ncbi.esearch(
                self._s.corpus_query,
                self._s.corpus_min_date,
                self._s.corpus_max_date,
                self._s.corpus_max_results,
            )
            atomic_write_text(
                area.corpus_config_file, json.dumps(config, indent=2, sort_keys=True)
            )
            atomic_write_text(area.pmids_file, json.dumps(pmids, indent=1))
            detail = "esearch"
        batches = math.ceil(len(pmids) / self._s.fetch_batch_size)
        return StageResult(items=len(pmids), batches=batches, detail=detail)

    # ── stage 2: fetch (fanned out, one activity per batch) ────────────────
    @activity.defn
    async def fetch_batch(self, inp: BatchInput) -> StageResult:
        """efetch one batch unless its XML is already staged."""
        await self._note_retry()
        area, size = self._area(inp.corpus_version), self._s.fetch_batch_size
        target = area.xml_batch(inp.index)
        pmids = self._pmids(inp.corpus_version)[
            inp.index * size : (inp.index + 1) * size
        ]
        if target.exists():
            await self._emit(
                IngestEventType.BATCH_FETCHED,
                f"batch {inp.index} already staged",
                batch=inp.index,
                cached=True,
            )
            return StageResult(items=len(pmids), batches=1, cached=1)
        activity.heartbeat(inp.index)
        xml = await self._ncbi.efetch_xml(pmids)
        if b"<PubmedArticleSet" not in xml[:2000]:
            raise NcbiRetryableError(
                f"efetch batch {inp.index}: unexpected response body"
            )
        atomic_write_bytes(target, xml)
        await self._emit(
            IngestEventType.BATCH_FETCHED,
            f"batch {inp.index} fetched ({len(pmids)} PMIDs)",
            batch=inp.index,
            cached=False,
        )
        return StageResult(items=len(pmids), batches=1)

    # ── stage 3: link (one long activity, resumes from heartbeat) ───────────
    @activity.defn
    async def link_all(self, corpus_version: str) -> StageResult:
        """elink every batch; heartbeat the last finished batch so a retry resumes after it."""
        await self._note_retry()
        area, size = self._area(corpus_version), self._s.link_batch_size
        pmids = self._pmids(corpus_version)
        total = math.ceil(len(pmids) / size)
        details = activity.info().heartbeat_details
        start = int(details[0]) + 1 if details else 0
        for index in range(start, total):
            target = area.elink_batch(index)
            if not target.exists():
                xml = await self._ncbi.elink_refs_xml(
                    pmids[index * size : (index + 1) * size]
                )
                try:
                    parse_elink_refs(xml)  # validate before staging
                except etree.XMLSyntaxError as exc:
                    raise NcbiRetryableError(
                        f"elink batch {index}: malformed XML"
                    ) from exc
                atomic_write_bytes(target, xml)
                await self._emit(
                    IngestEventType.BATCH_LINKED, f"batch {index} linked", batch=index
                )
            activity.heartbeat(index)
        return StageResult(
            items=len(pmids),
            batches=total,
            detail=f"resumed from batch {start}" if start else "",
        )

    # ── stage 4: enrich ───────────────────────────────────────────────────
    @activity.defn
    async def enrich(self, corpus_version: str) -> StageResult:
        """MeSH tree numbers + ancestors for every descriptor seen in the staged XML (cached)."""
        await self._note_retry()
        area, names = self._area(corpus_version), {}
        for index in range(self._xml_batches(corpus_version)):
            for paper in parse_pubmed_xml(area.xml_batch(index).read_bytes()).papers:
                names.update({m.ui: m.name for m in paper.mesh})
        hierarchy, stats = await enrich_mesh(
            names,
            area.mesh_cache_file,
            self._mesh,
            on_progress=lambda msg: activity.heartbeat(msg),
        )
        atomic_write_text(area.hierarchy_file, hierarchy.model_dump_json())
        return StageResult(
            items=stats.descriptors,
            detail=(
                f"{stats.ancestors} ancestor terms, {stats.without_tree_numbers} without tree numbers, "
                f"{stats.calls_made} lookup rounds"
            ),
        )

    # ── stage 5: parse/load ───────────────────────────────────────────────
    @activity.defn
    async def prepare_load(self, corpus_version: str) -> StageResult:
        """Parse all staged XML, disambiguate authors, write load-ready batches + skipped report."""
        await self._note_retry()
        area, papers, skipped = self._area(corpus_version), [], []
        for index in range(self._xml_batches(corpus_version)):
            result = parse_pubmed_xml(area.xml_batch(index).read_bytes())
            papers += result.papers
            skipped += result.skipped
            await self._emit(
                IngestEventType.BATCH_PARSED,
                f"batch {index} parsed: {len(result.papers)} papers, {len(result.skipped)} skipped",
                batch=index,
            )
        for record in skipped:
            await self._emit(
                IngestEventType.RECORD_SKIPPED,
                f"PMID {record.pmid}: {record.reason}",
                pmid=record.pmid,
                reason=record.reason,
            )
        resolution = resolve_authors(papers)
        size = self._s.load_batch_size
        batches = math.ceil(len(papers) / size)
        for index in range(batches):
            chunk = papers[index * size : (index + 1) * size]
            atomic_write_text(
                area.parsed_batch(index),
                json.dumps({"papers": [p.model_dump() for p in chunk]}),
            )
        atomic_write_text(area.authors_file, resolution.model_dump_json())
        atomic_write_text(
            area.skipped_file, json.dumps([s.model_dump() for s in skipped], indent=1)
        )
        return StageResult(
            items=len(papers),
            batches=batches,
            skipped=len(skipped),
            detail=f"{len(resolution.authors)} authors, {resolution.multi_paper_count()} with 2+ papers",
        )

    @activity.defn
    async def load_reference_data(self, corpus_version: str) -> StageResult:
        """Constraints, MeSH hierarchy and authors — everything paper batches will MATCH."""
        await self._note_retry()
        area, db = self._area(corpus_version), self._s.graph_database
        await ensure_schema(self._driver, db)
        hierarchy = MeshHierarchy.model_validate_json(
            area.hierarchy_file.read_text(encoding="utf-8")
        )
        await loader.load_mesh_hierarchy(self._driver, db, hierarchy)
        resolution = AuthorResolution.model_validate_json(
            area.authors_file.read_text(encoding="utf-8")
        )
        await loader.load_authors(self._driver, db, resolution.authors)
        return StageResult(
            items=len(resolution.authors), detail=f"{len(hierarchy.terms)} MeSH terms"
        )

    @activity.defn
    async def load_batch(self, inp: BatchInput) -> StageResult:
        """Write one parsed batch to Neo4j in one transaction."""
        await self._note_retry()
        area = self._area(inp.corpus_version)
        papers = [
            PaperRecord.model_validate(p)
            for p in read_json(area.parsed_batch(inp.index))["papers"]
        ]
        resolution = AuthorResolution.model_validate_json(
            area.authors_file.read_text(encoding="utf-8")
        )
        rows = build_batch_rows(papers, resolution.by_paper(), inp.corpus_version)
        written = await loader.load_paper_batch(
            self._driver, self._s.graph_database, rows
        )
        await self._emit(
            IngestEventType.PAPERS_WRITTEN,
            f"batch {inp.index}: {written} papers written",
            batch=inp.index,
            papers=written,
        )
        return StageResult(items=written, batches=1)

    @activity.defn
    async def finalize(self, corpus_version: str) -> dict:
        """Citations, orphan clean-up, stats → Redis; publishes the terminal `completed` event."""
        await self._note_retry()
        area, db, run_id = (
            self._area(corpus_version),
            self._s.graph_database,
            activity.info().workflow_id,
        )
        papers = self._parsed_papers(corpus_version)
        elink: dict[str, list[str]] = {}
        for index in range(
            math.ceil(len(self._pmids(corpus_version)) / self._s.link_batch_size)
        ):
            elink.update(parse_elink_refs(area.elink_batch(index).read_bytes()))
        report = reconcile_citations(
            {p.pmid: p.xml_references for p in papers},
            elink,
            corpus={p.pmid for p in papers},
        )
        atomic_write_text(
            area.citations_file, report.model_dump_json(exclude={"edges"}, indent=1)
        )
        await loader.load_citations(self._driver, db, report.edges)
        orphans = await loader.delete_orphan_authors(self._driver, db)
        stats = await compute_corpus_stats(self._driver, db, self._s.corpus_cutoff_year)
        skipped = [
            SkippedRecord.model_validate(s) for s in read_json(area.skipped_file)
        ]
        result = {
            "run_id": run_id,
            "corpus_version": corpus_version,
            "papers": stats["papers"],
            "authors": stats["node_counts"].get("Author", 0),
            "mesh_terms": stats["node_counts"].get("MeshTerm", 0),
            "journals": stats["node_counts"].get("Journal", 0),
            "citations": report.model_dump(exclude={"edges"}),
            "orphan_authors_removed": orphans,
            "skipped_records": [s.model_dump() for s in skipped],
            # Delta ingestion (§4.4) is not implemented: a version is always loaded in full.
            "added": stats["papers"],
            "removed": 0,
            "unchanged": 0,
            "corpus_stats": stats,
        }
        await self._events.set_json(CORPUS_STATS_KEY, stats)
        await self._events.set_json(ingest_result_key(run_id), result)
        await self._emit(
            IngestEventType.COMPLETED,
            f"ingestion complete: {stats['papers']} papers, {stats['cites_total']} citations",
            papers=stats["papers"],
            citations=stats["cites_total"],
        )
        return result

    @activity.defn
    async def publish_event(self, inp: EventInput) -> None:
        """Lets the (I/O-free) workflow publish stage-level events."""
        await self._events.publish_ingest(inp.run_id, inp.type, inp.content, inp.meta)
