import httpx
import pytest

from clients.ncbi import NcbiClient, NcbiPermanentError, NcbiRetryableError


class CountingLimiter:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def acquire(self, label: str = "") -> None:
        self.calls.append(label)


def make_client(handler, api_key: str = "") -> tuple[NcbiClient, CountingLimiter]:
    limiter = CountingLimiter()
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return NcbiClient(http, limiter, tool="t", email="e@x.org", api_key=api_key), limiter


async def test_esearch_returns_sorted_pmids_and_uses_limiter():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("esearch.fcgi")
        assert request.url.params["datetype"] == "pdat"
        return httpx.Response(200, json={"esearchresult": {"count": "3", "idlist": ["30", "10", "20"]}})

    client, limiter = make_client(handler)
    assert await client.esearch("q", "2005", "2025", max_results=400) == ["10", "20", "30"]
    assert limiter.calls == ["esearch"]


async def test_esearch_more_hits_than_max_is_permanent():
    def handler(request):
        return httpx.Response(200, json={"esearchresult": {"count": "900", "idlist": ["1"]}})

    client, _ = make_client(handler)
    with pytest.raises(NcbiPermanentError, match="900"):
        await client.esearch("q", "2005", "2025", max_results=400)


async def test_error_in_200_json_is_retryable():
    def handler(request):
        return httpx.Response(200, json={"esearchresult": {"ERROR": "Search Backend failed"}})

    client, _ = make_client(handler)
    with pytest.raises(NcbiRetryableError, match="Backend"):
        await client.esearch("q", "2005", "2025", max_results=400)


async def test_error_in_200_xml_is_retryable():
    def handler(request):
        return httpx.Response(200, content=b"<eFetchResult><ERROR>Unable to obtain query #1</ERROR></eFetchResult>")

    client, _ = make_client(handler)
    with pytest.raises(NcbiRetryableError):
        await client.efetch_xml(["1"])


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_throttling_and_server_errors_are_retryable(status):
    client, _ = make_client(lambda request: httpx.Response(status, text="busy"))
    with pytest.raises(NcbiRetryableError):
        await client.efetch_xml(["1"])


async def test_bad_request_is_permanent():
    client, _ = make_client(lambda request: httpx.Response(400, text="bad"))
    with pytest.raises(NcbiPermanentError):
        await client.efetch_xml(["1"])


async def test_elink_sends_one_id_param_per_pmid_and_api_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        seen["ids"] = body.count("id=")
        seen["key"] = "api_key=k" in body
        return httpx.Response(200, content=b"<eLinkResult></eLinkResult>")

    client, limiter = make_client(handler, api_key="k")
    await client.elink_refs_xml(["1", "2", "3"])
    assert seen == {"ids": 3, "key": True}
    assert limiter.calls == ["elink"]
