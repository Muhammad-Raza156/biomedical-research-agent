import pytest
from fastapi.testclient import TestClient

from config import ApiSettings
from main import create_app
from tests.fakes import FakeGateway, FakeRedis


@pytest.fixture
def fakes():
    return FakeGateway(), FakeRedis()


@pytest.fixture
def client(fakes):
    gateway, redis = fakes
    settings = ApiSettings.from_env({"GRAPH_HTTP_URL": "http://127.0.0.1:9", "SSE_BLOCK_MS": "50"})
    with TestClient(create_app(settings, gateway=gateway, redis=redis)) as c:
        yield c
