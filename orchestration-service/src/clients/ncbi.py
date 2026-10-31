"""Minimal async NCBI E-utilities client. Rate-limited; classifies errors; never retries itself."""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class NcbiRetryableError(Exception):
    """Transient failure (429, 5xx, network, <ERROR> in a 200 body). Temporal will retry."""


class NcbiPermanentError(Exception):
    """Failure that retrying cannot fix (bad request, corpus larger than configured max)."""


class Limiter(Protocol):
    """Anything with an async acquire(); RedisTokenBucket in production, a fake in tests."""

    async def acquire(self, label: str = "") -> None: ...


class NcbiClient:
    """Wraps esearch / efetch / elink with rate limiting and error classification."""

    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    def __init__(self, http: httpx.AsyncClient, limiter: Limiter, *, tool: str, email: str,
                 api_key: str = "") -> None:
        self._http = http
        self._limiter = limiter
        self._common = {"tool": tool, "email": email}
        if api_key:
            self._common["api_key"] = api_key

    async def esearch(self, term: str, mindate: str, maxdate: str, max_results: int) -> list[str]:
        """Return all PMIDs matching the query in the date range, sorted ascending."""
        params = {"db": "pubmed", "term": term, "datetype": "pdat", "mindate": mindate,
                  "maxdate": maxdate, "retmax": str(max_results), "retmode": "json"}
        response = await self._request("esearch.fcgi", "esearch", params)
        result = response.json().get("esearchresult", {})
        if "ERROR" in result:
            raise NcbiRetryableError(f"esearch error: {result['ERROR']}")
        count = int(result.get("count", 0))
        if count > max_results:
            raise NcbiPermanentError(
                f"query matches {count} papers but CORPUS_MAX_RESULTS={max_results}; narrow the query")
        return sorted(result.get("idlist", []), key=int)

    async def efetch_xml(self, pmids: list[str]) -> bytes:
        """Return raw PubmedArticleSet XML for up to ~200 PMIDs."""
        data = {"db": "pubmed", "id": ",".join(pmids), "retmode": "xml"}
        return (await self._request("efetch.fcgi", "efetch", data)).content

    async def elink_refs_xml(self, pmids: list[str]) -> bytes:
        """Return raw eLinkResult XML: one LinkSet per PMID with the PMIDs it cites."""
        data = {"dbfrom": "pubmed", "db": "pubmed", "linkname": "pubmed_pubmed_refs", "id": pmids}
        return (await self._request("elink.fcgi", "elink", data)).content

    async def _request(self, endpoint: str, label: str, params: dict[str, Any]) -> httpx.Response:
        await self._limiter.acquire(label)
        url = self.BASE_URL + endpoint
        payload = {**params, **self._common}
        try:
            response = await self._http.post(url, data=payload, timeout=120)
        except httpx.TransportError as exc:  # DNS, connection reset, timeout
            raise NcbiRetryableError(f"{label} network error: {exc!r}") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise NcbiRetryableError(f"{label} HTTP {response.status_code}")
        if response.status_code >= 400:
            raise NcbiPermanentError(f"{label} HTTP {response.status_code}: {response.text[:200]}")
        if b"<ERROR>" in response.content[:4096]:
            raise NcbiRetryableError(f"{label} returned <ERROR> in a 200 response")
        return response
