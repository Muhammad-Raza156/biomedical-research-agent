"""NLM MeSH SPARQL client: tree numbers for descriptors, and descriptors at tree numbers."""

from __future__ import annotations

from typing import Protocol

import httpx

from clients.ncbi import Limiter

MESH_BUCKET_KEY = "ratelimit:mesh"
BATCH = 50
_PREFIXES = """PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX meshv: <http://id.nlm.nih.gov/mesh/vocab#>
PREFIX mesh: <http://id.nlm.nih.gov/mesh/>
"""


class MeshRetryableError(Exception):
    """Transient SPARQL failure; Temporal retries the activity."""

class MeshPermanentError(Exception):
    """SPARQL failure that retrying cannot fix (e.g. 400 for a malformed query)."""


class MeshLookup(Protocol):
    """What enrichment needs; MeshClient in production, a fake in tests."""

    async def tree_numbers(self, uis: list[str]) -> dict[str, tuple[str, list[str]]]: ...

    async def descriptors_at(self, tns: list[str]) -> dict[str, tuple[str, str]]: ...


def _local(uri: str) -> str:
    """'http://id.nlm.nih.gov/mesh/D008687' → 'D008687'."""
    return uri.rsplit("/", 1)[-1]


class MeshClient:
    """Batched, rate-limited SPARQL queries against id.nlm.nih.gov."""

    SPARQL_URL = "https://id.nlm.nih.gov/mesh/sparql"

    def __init__(self, http: httpx.AsyncClient, limiter: Limiter) -> None:
        self._http = http
        self._limiter = limiter

    async def tree_numbers(self, uis: list[str]) -> dict[str, tuple[str, list[str]]]:
        """Q1: ui → (name, sorted tree numbers). Descriptors with no tree number are absent."""
        found: dict[str, tuple[str, set[str]]] = {}
        for start in range(0, len(uis), BATCH):
            values = " ".join(f"mesh:{ui}" for ui in uis[start:start + BATCH])
            rows = await self._select(
                f"SELECT ?d ?name ?tn WHERE {{ VALUES ?d {{ {values} }} "
                f"?d rdfs:label ?name ; meshv:treeNumber ?t . ?t rdfs:label ?tn }}")
            for row in rows:
                ui = _local(row["d"]["value"])
                name, tns = found.setdefault(ui, (row["name"]["value"], set()))
                tns.add(row["tn"]["value"])
        return {ui: (name, sorted(tns)) for ui, (name, tns) in found.items()}

    async def descriptors_at(self, tns: list[str]) -> dict[str, tuple[str, str]]:
        """Q2: tree number → (ui, name) of the descriptor at that position."""
        found: dict[str, tuple[str, str]] = {}
        for start in range(0, len(tns), BATCH):
            values = " ".join(f"mesh:{tn}" for tn in tns[start:start + BATCH])
            rows = await self._select(
                f"SELECT ?t ?d ?name WHERE {{ VALUES ?t {{ {values} }} ?d meshv:treeNumber ?t ; rdfs:label ?name }}")
            for row in rows:
                found[_local(row["t"]["value"])] = (_local(row["d"]["value"]), row["name"]["value"])
        return found

    async def _select(self, query: str) -> list[dict]:
        await self._limiter.acquire("mesh_sparql")
        try:
            response = await self._http.get(self.SPARQL_URL, timeout=60, params={
                "query": _PREFIXES + query, "format": "JSON", "limit": "10000"})
        except httpx.TransportError as exc:
            raise MeshRetryableError(f"mesh network error: {exc!r}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise MeshRetryableError(f"mesh HTTP {response.status_code}")
        if response.status_code >= 400:
            raise MeshPermanentError(f"mesh HTTP {response.status_code}: {response.text[:200]}")
        return response.json()["results"]["bindings"]
