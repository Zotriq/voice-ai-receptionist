import importlib.util
import json
from pathlib import Path

import httpx

spec = importlib.util.spec_from_file_location("create_assistant", Path(__file__).parents[1] / "scripts" / "create_assistant.py")
create_assistant = importlib.util.module_from_spec(spec)
spec.loader.exec_module(create_assistant)


def test_creates_tools_then_assistant(monkeypatch):
    monkeypatch.setenv("VAPI_API_KEY", "k")
    monkeypatch.setenv("PUBLIC_SERVER_URL", "https://example.com/")
    monkeypatch.setenv("VAPI_SECRET", "s")
    monkeypatch.setenv("FRONT_DESK_NUMBER", "+15550102040")
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append((request.url.path, body))
        return httpx.Response(201, json={"id": f"id{len(sent)}"})

    client = httpx.Client(base_url="https://api.vapi.ai", transport=httpx.MockTransport(handler))
    assistant_id = create_assistant.main(client)

    tools = [b for p, b in sent if p == "/tool"]
    assert len(tools) == 6 and tools[-1]["type"] == "transferCall"
    assert all(t["server"]["url"] == "https://example.com/vapi/webhook" for t in tools[:5])
    path, assistant = sent[-1]
    assert path == "/assistant" and assistant["model"]["toolIds"] == [f"id{i}" for i in range(1, 7)]
    assert assistant_id == "id7"
