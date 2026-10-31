"""Everything a node needs, injected once per run (no globals)."""

from __future__ import annotations

from dataclasses import dataclass

from agent.emitter import Emitter
from agent.ports import GraphReader, LLMClient, ThinkingSink
from agent.state import AgentState


@dataclass
class AgentSettings:
    """Loop limits and safety knobs (from Settings / .env)."""

    max_iterations: int = 12
    max_verifier_rejections: int = 2
    max_cypher_retries: int = 3
    cypher_timeout_seconds: float = 10.0
    cypher_max_limit: int = 200
    cutoff_year: int = 2016
    price_input_per_mtok: float = 0.0
    price_output_per_mtok: float = 0.0


@dataclass
class AgentDeps:
    """Injected dependencies. suppress_upto / resume_note are set per run by the runner (plan 09)."""

    llm: LLMClient
    graph: GraphReader
    sink: ThinkingSink
    settings: AgentSettings
    suppress_upto: int = 0   # highest step already in the stream (events <= this are not re-sent)
    resume_note: str = ""    # if set, emitted once as a `checkpoint` event before the next new event

    def emitter(self, node: str, state: AgentState) -> Emitter:
        """An Emitter continuing from the state's last step."""
        return Emitter(self, node, state.get("step", 0))
