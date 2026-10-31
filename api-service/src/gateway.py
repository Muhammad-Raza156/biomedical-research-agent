"""The only module in the API that talks to Temporal. Translates Temporal errors to domain errors."""

from __future__ import annotations

import asyncio
import logging

from shared.contracts import (
    CLARIFY_UPDATE,
    INGESTION_WORKFLOW,
    RESEARCH_WORKFLOW,
    TASK_QUEUE_INGEST,
    TASK_QUEUE_RESEARCH,
    IngestionInput,
    ResearchInput,
    ingest_workflow_id,
    research_workflow_id,
)
from temporalio.client import Client, WorkflowUpdateFailedError
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

log = logging.getLogger(__name__)


class NotFound(Exception):
    """Unknown workflow/run/query ID → 404."""


class Conflict(Exception):
    """Valid ID but the action doesn't fit its current state → 409."""


class Unavailable(Exception):
    """A dependency (Temporal) isn't reachable yet → 503."""


class WorkflowGateway:
    """Starts, inspects, updates and cancels workflows."""

    def __init__(self, address: str, namespace: str) -> None:
        self._address = address
        self._namespace = namespace
        self._client: Client | None = None

    async def connect_forever(self, max_delay: float = 30.0) -> None:
        """Background task: connect with exponential back-off; never crash the app."""
        delay = 1.0
        while self._client is None:
            try:
                self._client = await Client.connect(self._address, namespace=self._namespace)
                log.info("connected to temporal at %s", self._address)
            except Exception as exc:  # noqa: BLE001 - any failure here means "not up yet"; keep trying
                log.warning("temporal unavailable (%s); retrying in %.0fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)

    def _c(self) -> Client:
        if self._client is None:
            raise Unavailable("temporal is not reachable yet")
        return self._client

    async def start_ingest(self, corpus_version: str, continue_as_new_events: int) -> str:
        """Same ID + policies as the worker's auto-start → idempotent outcome."""
        run_id = ingest_workflow_id(corpus_version)
        try:
            await self._c().start_workflow(
                INGESTION_WORKFLOW,
                IngestionInput(corpus_version=corpus_version, continue_as_new_events=continue_as_new_events),
                id=run_id, task_queue=TASK_QUEUE_INGEST,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY)
        except WorkflowAlreadyStartedError:
            pass  # completed run for this version: reuse it
        return run_id

    async def start_query(self, query_id: str, question: str, clarify_timeout_seconds: int) -> None:
        """Start ResearchWorkflow; returns as soon as Temporal accepted it."""
        await self._c().start_workflow(
            RESEARCH_WORKFLOW,
            ResearchInput(query_id=query_id, question=question, clarify_timeout_seconds=clarify_timeout_seconds),
            id=research_workflow_id(query_id), task_queue=TASK_QUEUE_RESEARCH)

    async def status(self, workflow_id: str) -> str:
        """Temporal execution status name, e.g. 'RUNNING' or 'COMPLETED'."""
        try:
            description = await self._c().get_workflow_handle(workflow_id).describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                raise NotFound(f"unknown id {workflow_id}") from exc
            raise
        return description.status.name if description.status else "RUNNING"

    async def query(self, workflow_id: str, name: str) -> dict:
        """Run a workflow query (status / progress)."""
        return await self._c().get_workflow_handle(workflow_id).query(name)

    async def result(self, workflow_id: str) -> dict:
        """Result of a completed workflow."""
        return await self._c().get_workflow_handle(workflow_id).result()

    async def clarify(self, workflow_id: str, choice: str) -> None:
        """Send the clarify update; any rejection becomes Conflict (409)."""
        try:
            await self._c().get_workflow_handle(workflow_id).execute_update(CLARIFY_UPDATE, choice)
        except WorkflowUpdateFailedError as exc:   # our validator rejected it
            raise Conflict(str(exc.cause or exc)) from exc
        except RPCError as exc:                      # e.g. "workflow execution already completed"
            raise Conflict(str(exc)) from exc

    async def cancel(self, workflow_id: str) -> None:
        """Request cancellation (the workflow publishes the terminal event itself)."""
        await self._c().get_workflow_handle(workflow_id).cancel()

    async def healthy(self) -> bool:
        """True if connected and the frontend answers a health check."""
        if self._client is None:
            return False
        try:
            return await self._client.service_client.check_health()
        except Exception:  # noqa: BLE001 - a health probe reports "down", it never raises
            return False
