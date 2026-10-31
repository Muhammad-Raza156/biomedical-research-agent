"""Ask the agent a question from the terminal. Usage (from orchestration-service/, stack up):
    $env:PYTHONPATH="src;.."; python scripts/ask.py "Who are the authors of PMID 33516777?"
Keys and passwords come from ../.env (never from the command line, so they stay out of shell history).
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import redis.asyncio as aioredis
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from shared.contracts import LLM_BUCKET_KEY

from agent.deps import AgentDeps, AgentSettings
from agent.graph import build_graph
from agent.state import initial_state
from clients.llm import LangChainLLM
from clients.rate_limiter import RedisTokenBucket
from config import Settings
from graph.driver import make_driver
from graph.repository import Neo4jGraphReader


def load_dotenv(path: Path) -> None:
    """Copy KEY=VALUE lines into os.environ (real environment variables win). Provider SDKs read keys from there."""
    for line in path.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() and not key.lstrip().startswith("#"):
            os.environ.setdefault(key.strip(), value.strip())


class PrintSink:
    def __init__(self) -> None:
        self.last_step = 0

    async def emit(self, event):
        self.last_step = event.step
        print(f"[{event.step:>3}] {event.node:<16} {event.type:<20} {event.content}")
        meta = json.loads(event.meta) if event.meta else {}
        if event.type == "graph_query":
            print(" " * 6 + meta["cypher"].replace("\n", "\n" + " " * 6))
        if meta.get("reasons"):  # why a claim failed verification
            print(" " * 6 + "; ".join(meta["reasons"]))


async def main(question: str) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    s = Settings.from_env()
    redis = aioredis.from_url("redis://localhost:16379/0")  # host ports, not the in-network names in .env
    driver = make_driver("bolt://localhost:7687", s.graph_reader_user, s.graph_reader_password)
    try:
        llm = LangChainLLM(s.llm_model, s.llm_temperature,
                           RedisTokenBucket(redis, LLM_BUCKET_KEY, s.llm_requests_per_minute / 60),
                           ollama_base_url=s.ollama_base_url)
        settings = AgentSettings(
            max_iterations=s.agent_max_iterations, max_verifier_rejections=s.agent_max_verifier_rejections,
            max_cypher_retries=s.agent_max_cypher_retries, cypher_timeout_seconds=s.cypher_timeout_seconds,
            cypher_max_limit=s.cypher_max_limit, cutoff_year=s.corpus_cutoff_year,
            price_input_per_mtok=s.llm_price_input_per_mtok, price_output_per_mtok=s.llm_price_output_per_mtok)
        sink = PrintSink()
        deps = AgentDeps(llm=llm, graph=Neo4jGraphReader(driver, s.graph_database), sink=sink, settings=settings)
        graph = build_graph(deps, InMemorySaver())
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        out = await graph.ainvoke(initial_state("cli", question), config)
        while "__interrupt__" in out:
            options = out["__interrupt__"][0].value["options"]
            for i, option in enumerate(options):
                print(f"  {i}: {option['label']} ({option['id']}, {option['papers']} papers)")
            choice = options[int(input("choose: "))]["id"]
            deps.suppress_upto = sink.last_step  # clarify re-runs from its start; don't print its events twice
            out = await graph.ainvoke(Command(resume=choice), config)
        print(json.dumps(out["final"], indent=2))
    finally:
        await driver.close()
        await redis.aclose()


asyncio.run(main(sys.argv[1]))
