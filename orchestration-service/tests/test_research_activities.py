"""run_agent inside a Temporal activity context: answer persisted once, clarification passed up."""

from langgraph.checkpoint.memory import InMemorySaver
from temporalio.testing import ActivityEnvironment

from activities.research import AgentRunInput, ResearchActivities, TerminalInput
from agent.deps import AgentSettings
from agent.schemas import PlanOutput
from tests.agent_fakes import FakeGraph, FakeLLM
from tests.test_agent_graph import AMBIGUOUS, FACTS, PAPERS, ROWS, plan
from tests.test_runner_resume import script


class MemoryPublisher:
    """EventPublisher stand-in: streams and keys in dicts."""

    def __init__(self) -> None:
        self.streams: dict[str, list] = {}
        self.keys: dict[str, dict] = {}

    async def publish(self, stream_key, event):
        self.streams.setdefault(stream_key, []).append(event)
        return str(len(self.streams[stream_key]))

    async def last_event(self, stream_key):
        events = self.streams.get(stream_key)
        return events[-1] if events else None

    async def last_step(self, stream_key):
        last = await self.last_event(stream_key)
        return last.step if last else 0

    async def set_json(self, key, value, ttl_seconds=None):
        self.keys[key] = value


def make(llm, graph):
    publisher = MemoryPublisher()
    return ResearchActivities(llm, graph, InMemorySaver(), publisher, AgentSettings(), 60), publisher


async def test_answer_is_persisted_and_published_once_even_if_the_activity_is_retried():
    acts, pub = make(FakeLLM(script()), FakeGraph(papers=PAPERS, rows=[ROWS], facts=FACTS))
    env = ActivityEnvironment()
    first = await env.run(acts.run_agent, AgentRunInput("q1", "Who wrote PMID 33516777?"))
    again = await env.run(acts.run_agent, AgentRunInput("q1", "Who wrote PMID 33516777?"))  # retry after publish
    assert first.status == again.status == "completed" and again.answer == first.answer
    events = pub.streams["thinking:q1"]
    assert [e.type for e in events].count("answer") == 1 and events[-1].type == "answer"
    steps = [e.step for e in events]
    assert steps == sorted(steps) and len(steps) == len(set(steps))
    assert pub.keys["answer:q1"]["status"] == "completed" and pub.keys["answer:q1"]["claims_passed"] == 1


async def test_interrupt_is_returned_as_awaiting_input():
    llm = FakeLLM({**script(), PlanOutput: [plan(("author", "Smith J"))]})
    acts, pub = make(llm, FakeGraph(authors=AMBIGUOUS, papers=PAPERS, rows=[ROWS], facts=FACTS))
    out = await ActivityEnvironment().run(acts.run_agent, AgentRunInput("q2", "What did Smith J publish?"))
    assert out.status == "awaiting_input" and out.mention == "Smith J"
    assert [o["id"] for o in out.options] == ["orcid:A", "name:smith|j:2/1"]
    assert "answer:q2" not in pub.keys


async def test_terminal_event_is_published_once():
    acts, pub = make(FakeLLM({}), FakeGraph())
    env = ActivityEnvironment()
    await env.run(acts.publish_terminal, TerminalInput("q3", "cancelled", "query cancelled by the user"))
    await env.run(acts.publish_terminal, TerminalInput("q3", "cancelled", "query cancelled by the user"))
    events = pub.streams["thinking:q3"]
    assert len(events) == 1 and events[0].type == "error" and '"cancelled"' in events[0].meta
    assert pub.keys["answer:q3"]["status"] == "cancelled"
