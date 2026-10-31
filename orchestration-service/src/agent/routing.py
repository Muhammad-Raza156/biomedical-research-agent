"""Pure routing functions: state in, next node name out. The global iteration cap lives here."""

from __future__ import annotations

from langgraph.graph import END

from agent.deps import AgentSettings
from agent.state import AgentState


def _capped(state: AgentState, s: AgentSettings) -> bool:
    return state.get("iterations", 0) >= s.max_iterations


def after_plan(state: AgentState) -> str:
    """Ambiguity → clarify; otherwise start the query loop (or go straight to synthesis)."""
    if state.get("pending_clarification"):
        return "clarify"
    return "generate_cypher" if state.get("sub_questions") else "synthesize"


def after_validate(state: AgentState, s: AgentSettings) -> str:
    """Accepted → execute; rejected → rewrite (until retries or the global cap run out) → observe."""
    if state.get("cypher_ok"):
        return "execute"
    if state.get("cypher_attempts", 0) < s.max_cypher_retries and not _capped(state, s):
        return "generate_cypher"
    return "observe"


def after_execute(state: AgentState, s: AgentSettings) -> str:
    """Success → observe; runtime error → rewrite if attempts remain."""
    if state.get("exec_error") and state.get("cypher_attempts", 0) < s.max_cypher_retries and not _capped(state, s):
        return "generate_cypher"
    return "observe"


def after_observe(state: AgentState, s: AgentSettings) -> str:
    """Next sub-question, retry, or synthesize (done, no more sub-questions, or cap reached)."""
    if _capped(state, s) or state.get("observe_decision") == "done":
        return "synthesize"
    if state.get("current_index", 0) >= len(state.get("sub_questions", [])):
        return "synthesize"
    return "generate_cypher"


def after_verify(state: AgentState) -> str:
    """verify either produced the final answer (END) or asked for a revision."""
    return END if state.get("final") else "synthesize"
