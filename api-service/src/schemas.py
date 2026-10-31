"""Request/response models (the OpenAPI contract at /docs)."""

from __future__ import annotations

from pydantic import BaseModel, Field
from shared.contracts import QueryStatus


class QuestionIn(BaseModel):
    """POST /queries body."""

    question: str = Field(min_length=3, max_length=2000, examples=["Which journal published PMID 35269852?"])


class ChoiceIn(BaseModel):
    """POST /queries/{id}/clarify body: an option id or label from the clarification."""

    choice: str = Field(min_length=1, max_length=200)


class RunIdOut(BaseModel):
    """POST /ingest response."""

    run_id: str


class QueryIdOut(BaseModel):
    """POST /queries response."""

    query_id: str


class ActionOut(BaseModel):
    """Clarify / cancel response."""

    query_id: str
    status: str


class ClaimOut(BaseModel):
    """One verified claim (mirrors the agent's VerifiedClaim)."""

    text: str
    pmids: list[str]
    cypher: str
    verification_queries: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AnswerOut(BaseModel):
    """Final answer (mirrors the agent's FinalAnswer)."""

    answer: str
    claims: list[ClaimOut]
    claims_checked: int
    claims_passed: int
    claims_dropped: int
    notes: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class ClarificationOut(BaseModel):
    """What the user must choose between."""

    mention: str
    options: list[dict]


class QueryStatusOut(BaseModel):
    """GET /queries/{id}."""

    query_id: str
    status: QueryStatus
    clarification: ClarificationOut | None = None
    answer: AnswerOut | None = None
    error: str | None = None


class IngestStatusOut(BaseModel):
    """GET /ingest/{run_id}: progress while running, final counts when done."""

    run_id: str
    status: str
    stage: str | None = None
    progress: dict = Field(default_factory=dict)
    result: dict | None = None


class HealthOut(BaseModel):
    """GET /health."""

    status: str
    dependencies: dict[str, str]
