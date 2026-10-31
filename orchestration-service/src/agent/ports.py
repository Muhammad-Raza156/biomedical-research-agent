"""The only three things the agent needs from the outside world. No Temporal, no Neo4j imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar

from pydantic import BaseModel
from shared.contracts import StreamEvent

if TYPE_CHECKING:
    from agent.schemas import Assertion

T = TypeVar("T", bound=BaseModel)


@dataclass
class TokenUsage:
    """Tokens reported by the provider for one LLM call."""

    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ExplainInfo:
    """What EXPLAIN tells us about a query without running it."""

    operators: list[str]
    max_estimated_rows: float


class GraphUnavailable(Exception):
    """Infrastructure failure (Neo4j down/restarting). Not the query's fault: let Temporal retry the activity."""


class QueryFailed(Exception):
    """The query itself failed (syntax, timeout, type error). Fed back to generate_cypher."""


class LLMClient(Protocol):
    """Structured-output LLM. Implemented by clients.llm.LangChainLLM (any provider)."""

    model_name: str

    async def structured(self, system: str, user: str, schema: type[T]) -> tuple[T, TokenUsage]: ...


class GraphReader(Protocol):
    """Read-only graph access. Implemented by graph.repository.Neo4jGraphReader."""

    async def explain(self, cypher: str) -> ExplainInfo: ...

    async def read(self, cypher: str, timeout: float) -> list[dict]: ...

    async def resolve_author(self, text: str) -> list[dict]: ...

    async def resolve_mesh(self, text: str) -> list[dict]: ...

    async def resolve_pmid(self, pmid: str) -> dict | None: ...

    async def paper_status(self, pmids: list[str]) -> dict[str, dict]: ...

    async def check_assertion(self, assertion: Assertion) -> tuple[bool, str]: ...


class ThinkingSink(Protocol):
    """Where thinking events go (Redis stream in production, a list in tests)."""

    async def emit(self, event: StreamEvent) -> None: ...
