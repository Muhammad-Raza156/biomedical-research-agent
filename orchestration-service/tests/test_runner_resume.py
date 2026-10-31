"""Kill-and-resume at the LangGraph level (no Temporal): checkpoint + step de-duplication."""

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from agent.deps import AgentDeps, AgentSettings
from agent.graph import build_graph
from agent.runner import run_once
from agent.schemas import CypherOutput, ObserveOutput, PlanOutput, SynthOutput
from tests.agent_fakes import FakeGraph, FakeLLM, ListSink
from tests.test_agent_graph import (
    AMBIGUOUS,
    CYPHER,
    FACTS,
    GOOD_CLAIM,
    PAPERS,
    ROWS,
    plan,
)


class CrashOnce(FakeLLM):
    """Raises the first time `crash_on` is requested — like a worker dying mid-node."""

    def __init__(self, script, crash_on):
        super().__init__(script)
        self.crash_on, self.crashed = crash_on, False

    async def structured(self, system, user, schema):
        if schema is self.crash_on and not self.crashed:
            self.crashed = True
            self.calls.append(schema.__name__)
            raise RuntimeError("worker killed")
        return await super().structured(system, user, schema)


def script():
    return {PlanOutput: [plan()], CypherOutput: [CYPHER],
            ObserveOutput: [ObserveOutput(summary="ok", decision="next")],
            SynthOutput: [SynthOutput(claims=[GOOD_CLAIM])]}


async def test_resume_after_crash_continues_from_last_checkpoint_without_duplicate_steps():
    saver, sink = InMemorySaver(), ListSink()                 # the sink plays the Redis stream
    llm = CrashOnce(script(), crash_on=SynthOutput)
    graph_db = FakeGraph(papers=PAPERS, rows=[ROWS], facts=FACTS)

    deps1 = AgentDeps(llm=llm, graph=graph_db, sink=sink, settings=AgentSettings())
    with pytest.raises(RuntimeError):
        await run_once(build_graph(deps1, saver), deps1, "q1", "Who wrote PMID 33516777?")

    # "New activity attempt": fresh deps, suppress everything already in the stream.
    deps2 = AgentDeps(llm=llm, graph=graph_db, sink=sink, settings=AgentSettings(),
                      suppress_upto=sink.events[-1].step)
    outcome = await run_once(build_graph(deps2, saver), deps2, "q1", "Who wrote PMID 33516777?")

    assert outcome.status == "completed" and outcome.final["claims_passed"] == 1
    steps = [e.step for e in sink.events]
    assert steps == sorted(steps) and len(steps) == len(set(steps))     # monotonic, no duplicates
    assert llm.calls.count("PlanOutput") == 1                           # plan NOT re-run
    assert llm.calls.count("CypherOutput") == 1
    assert llm.calls.count("SynthOutput") == 2                          # the in-flight node re-ran once
    checkpoints = [e for e in sink.events if e.type == "checkpoint"]
    assert len(checkpoints) == 1 and "synthesize" in checkpoints[0].content
    assert [e.node for e in sink.events if e.type == "node_enter"].count("plan") == 1


async def test_finished_run_is_not_rerun():
    saver, sink = InMemorySaver(), ListSink()
    llm = FakeLLM(script())
    deps = AgentDeps(llm=llm, graph=FakeGraph(papers=PAPERS, rows=[ROWS], facts=FACTS), sink=sink,
                     settings=AgentSettings())
    first = await run_once(build_graph(deps, saver), deps, "q1", "q")
    again = await run_once(build_graph(deps, saver), deps, "q1", "q")   # e.g. retry after answer published
    assert again.final == first.final
    assert llm.calls.count("PlanOutput") == 1


async def test_interrupt_then_resume_with_choice():
    saver, sink = InMemorySaver(), ListSink()
    llm = FakeLLM({PlanOutput: [plan(("author", "Smith J"))], CypherOutput: [CYPHER],
                   ObserveOutput: [ObserveOutput(summary="ok", decision="next")],
                   SynthOutput: [SynthOutput(claims=[GOOD_CLAIM])]})
    deps = AgentDeps(llm=llm, graph=FakeGraph(authors=AMBIGUOUS, papers=PAPERS, rows=[ROWS], facts=FACTS),
                     sink=sink, settings=AgentSettings())
    paused = await run_once(build_graph(deps, saver), deps, "q2", "What did Smith J publish?")
    assert paused.status == "awaiting_input" and paused.interrupt["mention"] == "Smith J"
    still = await run_once(build_graph(deps, saver), deps, "q2", "What did Smith J publish?")  # no choice yet
    assert still.status == "awaiting_input"
    deps.suppress_upto = sink.events[-1].step
    done = await run_once(build_graph(deps, saver), deps, "q2", "What did Smith J publish?", "orcid:A")
    assert done.status == "completed"
    steps = [e.step for e in sink.events]
    assert len(steps) == len(set(steps))
