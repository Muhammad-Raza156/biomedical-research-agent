"""The research agent StateGraph (no Temporal imports; see plan 09 for the durable wrapper)."""

from __future__ import annotations

from functools import partial

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from agent import routing
from agent.deps import AgentDeps
from agent.nodes.answer import make_synthesize, make_verify
from agent.nodes.cypher import make_execute, make_generate_cypher, make_validate_cypher
from agent.nodes.observe import make_observe
from agent.nodes.plan import make_clarify, make_plan
from agent.state import AgentState


def build_graph(deps: AgentDeps, checkpointer: BaseCheckpointSaver | None = None):
    """Wire nodes and conditional edges; compile with an optional checkpointer."""
    s = deps.settings
    g = StateGraph(AgentState)
    g.add_node("plan", make_plan(deps))
    g.add_node("clarify", make_clarify(deps))
    g.add_node("generate_cypher", make_generate_cypher(deps))
    g.add_node("validate_cypher", make_validate_cypher(deps))
    g.add_node("execute", make_execute(deps))
    g.add_node("observe", make_observe(deps))
    g.add_node("synthesize", make_synthesize(deps))
    g.add_node("verify", make_verify(deps))

    g.add_edge(START, "plan")
    g.add_conditional_edges("plan", routing.after_plan, ["clarify", "generate_cypher", "synthesize"])
    g.add_edge("clarify", "plan")
    g.add_edge("generate_cypher", "validate_cypher")
    g.add_conditional_edges("validate_cypher", partial(routing.after_validate, s=s),
                            ["execute", "generate_cypher", "observe"])
    g.add_conditional_edges("execute", partial(routing.after_execute, s=s), ["observe", "generate_cypher"])
    g.add_conditional_edges("observe", partial(routing.after_observe, s=s), ["generate_cypher", "synthesize"])
    g.add_edge("synthesize", "verify")
    g.add_conditional_edges("verify", routing.after_verify, ["synthesize", END])
    return g.compile(checkpointer=checkpointer)
