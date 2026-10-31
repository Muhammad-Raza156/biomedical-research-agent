"""Fakes for agent tests: scripted LLM, in-memory graph, list sink."""

from __future__ import annotations

from shared.contracts import StreamEvent

from agent.deps import AgentDeps, AgentSettings
from agent.ports import ExplainInfo, TokenUsage
from agent.schemas import Assertion


class FakeLLM:
    """Returns scripted responses per schema class, in order. Raises if the script runs out."""

    model_name = "fake"

    def __init__(self, script: dict[type, list]) -> None:
        self._script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []
        self.prompts: list[str] = []

    async def structured(self, system, user, schema):
        self.calls.append(schema.__name__)
        self.prompts.append(user)
        responses = self._script.get(schema, [])
        if not responses:
            raise AssertionError(f"FakeLLM has no scripted {schema.__name__} left")
        return responses.pop(0), TokenUsage(input_tokens=100, output_tokens=20)


class FakeGraph:
    """Answers resolution, EXPLAIN, reads and assertion checks from in-memory tables."""

    def __init__(self, *, authors=None, mesh=None, papers=None, rows=None, explain_ops=None, facts=None):
        self.authors = authors or {}        # lower-cased text → [{id, label, papers}]
        self.mesh = mesh or {}              # lower-cased text → [{id, label, papers}]
        self.papers = papers or {}          # pmid → {"in_corpus": bool, "retracted": bool, "label": title}
        self.rows = rows or []              # list of row-lists returned by successive read() calls
        self.explain_ops = explain_ops or ["ProduceResults", "NodeByLabelScan"]
        self.facts = facts or set()         # {(kind, pmid, value)} that check_assertion accepts
        self.reads: list[str] = []

    async def explain(self, cypher):
        return ExplainInfo(operators=list(self.explain_ops), max_estimated_rows=10)

    async def read(self, cypher, timeout):
        self.reads.append(cypher)
        return self.rows.pop(0) if self.rows else []

    async def resolve_author(self, text):
        return self.authors.get(text.lower(), [])

    async def resolve_mesh(self, text):
        return self.mesh.get(text.lower(), [])

    async def resolve_pmid(self, pmid):
        p = self.papers.get(pmid)
        return {"id": pmid, "label": p["label"], "retracted": p["retracted"]} if p else None

    async def paper_status(self, pmids):
        return {p: {"in_corpus": self.papers[p]["in_corpus"], "retracted": self.papers[p]["retracted"]}
                for p in pmids if p in self.papers}

    async def check_assertion(self, assertion: Assertion):
        value = assertion.author or assertion.mesh or assertion.year or assertion.target_pmid or assertion.journal
        return (assertion.kind, assertion.pmid, value) in self.facts, f"// check {assertion.kind}"


class ListSink:
    """Collects events in memory."""

    def __init__(self) -> None:
        self.events: list[StreamEvent] = []

    async def emit(self, event: StreamEvent) -> None:
        self.events.append(event)

    def types(self) -> list[str]:
        return [e.type for e in self.events]


def make_deps(llm, graph, **settings) -> tuple[AgentDeps, ListSink]:
    sink = ListSink()
    return AgentDeps(llm=llm, graph=graph, sink=sink, settings=AgentSettings(**settings)), sink
