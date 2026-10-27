import httpx
import pytest

from clients.mesh import MeshClient, MeshRetryableError


class NoLimit:
    async def acquire(self, label: str = "") -> None:
        return None


def binding(**values):
    return {k: {"type": "literal", "value": v} for k, v in values.items()}


async def test_tree_numbers_groups_rows_per_descriptor():
    def handler(request: httpx.Request) -> httpx.Response:
        assert "mesh:D008687" in request.url.params["query"]
        return httpx.Response(200, json={"results": {"bindings": [
            binding(d="http://id.nlm.nih.gov/mesh/D008687", name="Metformin", tn="D02.078.370.141.450"),
            binding(d="http://id.nlm.nih.gov/mesh/D008687", name="Metformin", tn="D27.505"),
        ]}})

    client = MeshClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), NoLimit())
    result = await client.tree_numbers(["D008687"])
    assert result == {"D008687": ("Metformin", ["D02.078.370.141.450", "D27.505"])}


async def test_descriptors_at_maps_tree_number_to_descriptor():
    def handler(request):
        return httpx.Response(200, json={"results": {"bindings": [
            binding(t="http://id.nlm.nih.gov/mesh/C04", d="http://id.nlm.nih.gov/mesh/D009369", name="Neoplasms"),
        ]}})

    client = MeshClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)), NoLimit())
    assert await client.descriptors_at(["C04"]) == {"C04": ("D009369", "Neoplasms")}


async def test_server_error_is_retryable():
    client = MeshClient(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503))), NoLimit())
    with pytest.raises(MeshRetryableError):
        await client.tree_numbers(["D1"])
