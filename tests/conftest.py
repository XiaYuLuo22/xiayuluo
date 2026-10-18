import json

from fastapi.testclient import TestClient
import httpx
import pytest

from assistant.config import Settings
from assistant.memory import MemoryStore
from assistant.server import create_app


def model_stream(*deltas, finish="stop"):
    items = [{"choices": [{"delta": delta, "finish_reason": None}]} for delta in deltas]
    if finish is not None:
        items.append({"choices": [{"delta": {}, "finish_reason": finish}]})
    return "".join("data: " + json.dumps(item, ensure_ascii=False) + "\n\n" for item in items) + "data: [DONE]\n\n"


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite3")
    yield memory
    memory.close()


@pytest.fixture
def client_factory(tmp_path):
    clients = []
    def build(stream=None, settings=None, handler=None):
        records = []
        def default_handler(request):
            records.append(json.loads(request.content))
            return httpx.Response(200, text=stream or model_stream({"content": "你好"}, {"content": "，一起开始吧。"}),
                                  headers={"Content-Type": "text/event-stream"})
        settings = settings or Settings(api_key="test-only-key", data_dir=str(tmp_path))
        app = create_app(settings, transport=httpx.MockTransport(handler or default_handler))
        client = TestClient(app, base_url="http://localhost")
        client.__enter__()
        client.headers["X-Assistant-Token"] = client.get('/api/bootstrap').json()['token']
        clients.append(client)
        return client, records
    yield build
    for client in clients:
        client.__exit__(None, None, None)


def events(response):
    return [json.loads(block[6:]) for block in response.text.strip().split("\n\n") if block.startswith("data: ")]
