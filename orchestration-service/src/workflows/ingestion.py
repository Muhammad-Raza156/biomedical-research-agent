"""IngestionWorkflow: deterministic orchestration of the five ingestion stages. No I/O here."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from shared.contracts import (
        INGESTION_WORKFLOW,
        PROGRESS_QUERY,
        IngestEventType,
        IngestionInput,
    )

    from activities.ingestion import IngestionActivities
    from activities.types import BatchInput, EventInput, StageResult

PERMANENT_ERRORS = [
    "NcbiPermanentError",
    "MeshPermanentError",
    "MalformedXmlError",
    "CorpusConfigMismatch",
]
STAGES = ("discover", "fetch", "link", "enrich", "parse_load")


def retry_policy(max_attempts: int, max_interval_s: int = 60) -> RetryPolicy:
    """Exponential back-off: 2s, 4s, 8s … capped; permanent errors are never retried."""
    return RetryPolicy(
        initial_interval=timedelta(seconds=2),
        backoff_coefficient=2.0,
        maximum_interval=timedelta(seconds=max_interval_s),
        maximum_attempts=max_attempts,
        non_retryable_error_types=PERMANENT_ERRORS,
    )


NCBI_RETRY = retry_policy(max_attempts=8)  # network + rate limits
DB_RETRY = retry_policy(
    max_attempts=10, max_interval_s=30
)  # Neo4j restarts / transient errors
EVENT_RETRY = retry_policy(max_attempts=5, max_interval_s=10)


@workflow.defn(name=INGESTION_WORKFLOW)
class IngestionWorkflow:
    """discover → fetch (fan-out) → link → enrich → parse/load (+ finalize)."""

    def __init__(self) -> None:
        self._stage = "starting"
        self._counts: dict[str, int] = {}
        self._result: dict = {}

    @workflow.query(name=PROGRESS_QUERY)
    def progress(self) -> dict:
        """Current stage and counts (read by GET /ingest/{run_id} while running)."""
        return {"stage": self._stage, "counts": dict(self._counts)}

    @workflow.run
    async def run(self, inp: IngestionInput) -> dict:
        self._counts = dict(inp.counts)
        run_id = workflow.info().workflow_id
        handlers = {
            "discover": self._discover,
            "fetch": self._fetch,
            "link": self._link,
            "enrich": self._enrich,
            "parse_load": self._parse_load,
        }
        try:
            for index in range(inp.start_stage, len(STAGES)):
                self._stage = STAGES[index]
                await self._event(
                    run_id,
                    IngestEventType.STAGE_STARTED,
                    f"stage {self._stage} started",
                )
                summary = await handlers[self._stage](inp.corpus_version)
                await self._event(
                    run_id,
                    IngestEventType.STAGE_COMPLETED,
                    f"stage {self._stage}: {summary}",
                )
                more_stages = index + 1 < len(STAGES)
                if (
                    more_stages
                    and workflow.info().get_current_history_length()
                    > inp.continue_as_new_events
                ):
                    workflow.continue_as_new(
                        IngestionInput(
                            corpus_version=inp.corpus_version,
                            start_stage=index + 1,
                            counts=self._counts,
                            continue_as_new_events=inp.continue_as_new_events,
                        )
                    )
        except ActivityError as err:
            reason = str(err.cause) if err.cause else str(err)
            await self._event(
                run_id,
                IngestEventType.ERROR,
                f"ingestion failed in stage {self._stage}: {reason}",
            )
            raise ApplicationError(
                f"ingestion failed in stage {self._stage}: {reason}", non_retryable=True
            ) from err
        self._stage = "completed"
        return self._result

    # ── stages ────────────────────────────────────────────────────────────
    async def _discover(self, version: str) -> str:
        r: StageResult = await workflow.execute_activity_method(
            IngestionActivities.discover,
            version,
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=NCBI_RETRY,
        )
        self._counts.update(pmids=r.items, fetch_batches=r.batches)
        return f"{r.items} PMIDs pinned ({r.detail})"

    async def _fetch(self, version: str) -> str:
        results = await asyncio.gather(
            *(
                workflow.execute_activity_method(
                    IngestionActivities.fetch_batch,
                    BatchInput(version, i),
                    start_to_close_timeout=timedelta(minutes=3),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=NCBI_RETRY,
                )
                for i in range(self._counts["fetch_batches"])
            )
        )
        cached = sum(r.cached for r in results)
        self._counts.update(batches_fetched=len(results), batches_cached=cached)
        return f"{len(results)} batches ({cached} already staged)"

    async def _link(self, version: str) -> str:
        r: StageResult = await workflow.execute_activity_method(
            IngestionActivities.link_all,
            version,
            start_to_close_timeout=timedelta(minutes=30),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=NCBI_RETRY,
        )
        self._counts["link_batches"] = r.batches
        return f"{r.batches} elink batches {r.detail}".strip()

    async def _enrich(self, version: str) -> str:
        r: StageResult = await workflow.execute_activity_method(
            IngestionActivities.enrich,
            version,
            start_to_close_timeout=timedelta(minutes=10),
            heartbeat_timeout=timedelta(seconds=60),
            retry_policy=NCBI_RETRY,
        )
        self._counts["mesh_descriptors"] = r.items
        return f"{r.items} descriptors; {r.detail}"

    async def _parse_load(self, version: str) -> str:
        prepared: StageResult = await workflow.execute_activity_method(
            IngestionActivities.prepare_load,
            version,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DB_RETRY,
        )
        self._counts.update(
            papers_parsed=prepared.items, records_skipped=prepared.skipped
        )
        await workflow.execute_activity_method(
            IngestionActivities.load_reference_data,
            version,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DB_RETRY,
        )
        written = 0
        for index in range(
            prepared.batches
        ):  # sequential: avoids lock contention on shared nodes
            r: StageResult = await workflow.execute_activity_method(
                IngestionActivities.load_batch,
                BatchInput(version, index),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=DB_RETRY,
            )
            written += r.items
        self._counts["papers_written"] = written
        self._result = await workflow.execute_activity_method(
            IngestionActivities.finalize,
            version,
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DB_RETRY,
        )
        return f"{written} papers written, {prepared.skipped} skipped"

    async def _event(self, run_id: str, type: str, content: str) -> None:
        await workflow.execute_activity_method(
            IngestionActivities.publish_event,
            EventInput(run_id, type, content),
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=EVENT_RETRY,
        )
