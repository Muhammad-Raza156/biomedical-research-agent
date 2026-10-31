"""Pydantic schemas the LLM must fill, plus the final answer shape."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── LLM outputs ─────────────────────────────────────────────────────────────


class EntityMention(BaseModel):
    """Something in the question that must be looked up in the graph."""

    kind: Literal["author", "mesh", "pmid"]
    text: str = Field(description="exactly as written in the question")


class PlanOutput(BaseModel):
    """plan node output."""

    sub_questions: list[str] = Field(description="1-4 steps, each answerable by one read-only Cypher query")
    entities: list[EntityMention] = Field(default_factory=list)
    definitions: list[str] = Field(default_factory=list,
                                   description="operational definitions of fuzzy terms, one sentence each")


class CypherOutput(BaseModel):
    """generate_cypher node output."""

    cypher: str = Field(description="one read-only Cypher query, no semicolon, with LIMIT")
    reasoning: str = Field(description="one sentence: why this query answers the sub-question")


class ObserveOutput(BaseModel):
    """observe node output."""

    summary: str = Field(description="what the rows show, in one or two sentences")
    decision: Literal["next", "retry", "done"] = Field(
        description="next = move to the next sub-question; retry = rewrite the query; done = enough evidence")
    retry_hint: str = Field(default="", description="if retry: what to change in the query")


class Assertion(BaseModel):
    """A machine-checkable fact a claim relies on (verified with a fixed query)."""

    kind: Literal["authored", "mentions", "published_before", "published_from", "published_in_year",
                  "cites", "in_journal", "retracted"]
    pmid: str
    author: str | None = Field(default=None, description="author_id or exact display_name (authored)")
    mesh: str | None = Field(default=None, description="MeSH UI or exact name (mentions; descendants count)")
    year: int | None = Field(default=None, description="year for published_* kinds")
    target_pmid: str | None = Field(default=None, description="cited PMID (cites)")
    journal: str | None = Field(default=None, description="ISSN or exact journal title (in_journal)")


class ClaimDraft(BaseModel):
    """One claim proposed by synthesize."""

    text: str
    pmids: list[str]
    evidence_index: int = Field(description="index of the evidence item whose rows support this claim")
    assertions: list[Assertion] = Field(default_factory=list)


class SynthOutput(BaseModel):
    """synthesize node output."""

    claims: list[ClaimDraft] = Field(default_factory=list)
    no_answer_reason: str = Field(default="", description="if no claim can be supported: why, in one sentence")


# ── Final answer (returned by the agent, persisted by the activity) ──────────


class VerifiedClaim(BaseModel):
    """A claim that passed verification."""

    text: str
    pmids: list[str]
    cypher: str
    verification_queries: list[str]
    warnings: list[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    """What GET /queries/{id} returns as `answer`."""

    answer: str
    claims: list[VerifiedClaim]
    claims_checked: int
    claims_passed: int
    claims_dropped: int
    notes: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
