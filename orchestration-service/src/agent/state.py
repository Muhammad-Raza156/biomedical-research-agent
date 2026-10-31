"""Typed agent state. Every key is JSON-serialisable so the checkpointer can store it."""

from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """State carried between nodes (total=False: nodes return only the keys they change)."""

    query_id: str
    question: str
    step: int                       # last thinking-event step number (checkpointed)
    iterations: int                 # global loop counter across ALL loops
    tokens: dict[str, int]          # {"input": n, "output": m}
    sub_questions: list[str]
    definitions: list[str]
    current_index: int              # which sub-question we're on
    mentions: list[dict]            # [{kind, text}] from plan
    entities: dict[str, dict]       # mention text → {kind, id, label}
    unresolved: list[str]           # mentions with no match in the graph
    pending_clarification: dict | None
    notes: list[str]                # shown to the user (e.g. "proceeded with most likely option")
    current_cypher: str
    cypher_ok: bool
    cypher_feedback: str            # rejection / error / retry hint for generate_cypher
    cypher_attempts: int            # attempts for the current sub-question
    exec_error: str
    evidence: list[dict]            # [{index, sub_question, cypher, rows, row_count}]
    observe_decision: str
    draft: dict | None              # SynthOutput as dict
    verifier_feedback: str
    verifier_rejections: int
    final: dict | None              # FinalAnswer as dict → graph ends


def initial_state(query_id: str, question: str) -> AgentState:
    """The input for a brand-new run."""
    return AgentState(query_id=query_id, question=question, step=0, iterations=0,
                      tokens={"input": 0, "output": 0}, current_index=0, entities={}, unresolved=[],
                      pending_clarification=None, notes=[], evidence=[], cypher_attempts=0,
                      verifier_rejections=0, draft=None, final=None)
