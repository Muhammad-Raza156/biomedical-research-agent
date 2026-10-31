"""Decide how to (re)enter the graph for a thread: start, resume after a crash, or resume an interrupt.

No Temporal imports: the activity in activities/research.py wraps this with heartbeats.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langgraph.types import Command

from agent.deps import AgentDeps
from agent.state import initial_state


@dataclass
class RunOutcome:
    """Either paused for the user (interrupt payload) or finished (final answer)."""

    status: Literal["awaiting_input", "completed"]
    interrupt: dict | None = None
    final: dict | None = None
    step: int = 0


async def run_once(graph, deps: AgentDeps, query_id: str, question: str,
                   resume_value: str | None = None) -> RunOutcome:
    """Run the graph for `query_id` until it finishes or pauses, continuing from any checkpoint."""
    config = {"configurable": {"thread_id": query_id}}
    snapshot = await graph.aget_state(config)
    values = snapshot.values or {}
    pending = [i for task in snapshot.tasks for i in task.interrupts]

    if values.get("final"):                       # already finished (e.g. retry after the answer was saved)
        return RunOutcome("completed", final=values["final"], step=values.get("step", 0))
    if pending:                                   # paused in clarify()
        if resume_value is None:
            return RunOutcome("awaiting_input", interrupt=pending[0].value, step=values.get("step", 0))
        graph_input = Command(resume=resume_value)
    elif snapshot.next:                           # a previous attempt died mid-run
        deps.resume_note = (f"resumed from checkpoint after a worker restart; "
                            f"continuing at node '{snapshot.next[0]}'")
        graph_input = None
    else:                                         # brand-new thread
        graph_input = initial_state(query_id, question)

    out = await graph.ainvoke(graph_input, config)
    if "__interrupt__" in out:
        return RunOutcome("awaiting_input", interrupt=out["__interrupt__"][0].value, step=out.get("step", 0))
    return RunOutcome("completed", final=out["final"], step=out.get("step", 0))
