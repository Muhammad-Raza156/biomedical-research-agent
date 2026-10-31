"""POST /queries, GET /queries/{id}, stream, clarify, cancel."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Request
from shared.contracts import (
    STATUS_QUERY,
    QueryStatus,
    answer_key,
    research_workflow_id,
    thinking_stream_key,
)

from gateway import Conflict
from schemas import (
    ActionOut,
    AnswerOut,
    ChoiceIn,
    QueryIdOut,
    QueryStatusOut,
    QuestionIn,
)
from sse import require_stream, sse_response

router = APIRouter(tags=["queries"])


@router.post("/queries", status_code=202, response_model=QueryIdOut)
async def create_query(body: QuestionIn, request: Request) -> QueryIdOut:
    """Start ResearchWorkflow and return immediately."""
    query_id = uuid.uuid4().hex[:16]
    await request.app.state.gateway.start_query(query_id, body.question,
                                                request.app.state.settings.clarify_timeout_seconds)
    return QueryIdOut(query_id=query_id)


@router.get("/queries/{query_id}", response_model=QueryStatusOut)
async def get_query(query_id: str, request: Request) -> QueryStatusOut:
    """Status, clarification question, final answer with claims and token usage."""
    gateway, redis = request.app.state.gateway, request.app.state.redis
    workflow_id = research_workflow_id(query_id)
    status = await gateway.status(workflow_id)
    if status == "RUNNING":
        live = await gateway.query(workflow_id, STATUS_QUERY)
        return QueryStatusOut(query_id=query_id, status=live["status"], clarification=live["clarification"])
    stored = await redis.get(answer_key(query_id))
    data = json.loads(stored) if stored else {}
    if status == "COMPLETED":
        data = data or await gateway.result(workflow_id)
        return QueryStatusOut(query_id=query_id, status=QueryStatus.COMPLETED, answer=AnswerOut.model_validate(data))
    if status == "CANCELED":
        return QueryStatusOut(query_id=query_id, status=QueryStatus.CANCELLED, error=data.get("error", "cancelled"))
    return QueryStatusOut(query_id=query_id, status=QueryStatus.FAILED,
                          error=data.get("error", f"workflow {status.lower()}"))


@router.get("/queries/{query_id}/stream")
async def query_stream(query_id: str, request: Request):
    """SSE of thinking:{query_id}: replay, then live; honours Last-Event-ID; closes after answer/error."""
    key = thinking_stream_key(query_id)
    await require_stream(request, key, research_workflow_id(query_id))
    return sse_response(request, key)


@router.post("/queries/{query_id}/clarify", response_model=ActionOut)
async def clarify(query_id: str, body: ChoiceIn, request: Request) -> ActionOut:
    """Send the user's choice (409 if the query isn't awaiting input)."""
    workflow_id = research_workflow_id(query_id)
    await request.app.state.gateway.status(workflow_id)       # 404 for unknown IDs
    await request.app.state.gateway.clarify(workflow_id, body.choice)
    return ActionOut(query_id=query_id, status="accepted")


@router.post("/queries/{query_id}/cancel", status_code=202, response_model=ActionOut)
async def cancel(query_id: str, request: Request) -> ActionOut:
    """Cancel a running query; the workflow publishes a terminal error event (reason: cancelled)."""
    workflow_id = research_workflow_id(query_id)
    status = await request.app.state.gateway.status(workflow_id)
    if status != "RUNNING":
        raise Conflict(f"query already {status.lower()}")
    await request.app.state.gateway.cancel(workflow_id)
    return ActionOut(query_id=query_id, status="cancelling")
